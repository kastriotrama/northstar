import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  input,
  model,
  output,
  viewChild,
} from '@angular/core';

import { fieldName } from '../core/match-reasons';
import type {
  KTypeCandidate,
  KTypeChoiceHistory,
  KTypeChoiceHistoryEntry,
  KTypeChoiceRequest,
  VehicleMatchLookup,
} from '../core/models';

/** An action on its way to the server: asked about first, then sent, then perhaps resent. */
export interface PendingChoice {
  /** Minted once; a retry resends it with the same body. */
  operationId: string;
  body: KTypeChoiceRequest;
  needsConfirm: boolean;
}

export interface ChoiceError {
  message: string;
  /** The same operation can be sent again (network or server trouble). */
  retry: boolean;
}

/** The id the "enter your name" hint carries, for the choose buttons' `aria-describedby`. */
export const NAME_HINT_ID = 'ktype-choice-name-hint';
/** The id of what the Confirm button is about to do, for its `aria-describedby`. */
const CONFIRM_LINES_ID = 'ktype-choice-confirm-lines';

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.join(', ') || '—';
  return String(value);
}

/**
 * A person's KType choice for one car: what is stored, whether it still fits, and the
 * controls to record, change or withdraw it. Presentational -- the candidate list is
 * projected between the banner and the "none of these" button, and the container sends.
 */
@Component({
  selector: 'ns-ktype-choice',
  imports: [DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (choice(); as current) {
      @if (current.status === 'withdrawn') {
        <p class="banner banner--muted">
          An earlier choice was withdrawn by {{ current.reviewer }} on
          {{ current.created_at | date: 'yyyy-MM-dd' }}. The automatic result applies.
        </p>
      } @else {
        <div class="banner">
          @if (current.status === 'chosen') {
            <p>
              <b>KType <span class="mono">{{ current.ktype }}</span> · chosen by a person</b>
              — {{ current.reviewer }}, {{ current.created_at | date: 'yyyy-MM-dd HH:mm' }}
            </p>
          } @else {
            <p>
              <b>No KType · “none of these”</b> recorded by {{ current.reviewer }},
              {{ current.created_at | date: 'yyyy-MM-dd' }}
            </p>
          }
          @if (current.reason) {
            <p class="quote">“{{ current.reason }}”</p>
          }
          <button
            type="button"
            [disabled]="blocked()"
            [attr.aria-describedby]="named() ? null : hintId"
            (click)="withdraw.emit()"
          >
            Withdraw choice
          </button>
        </div>

        @if (current.needs_review) {
          <div class="stale" role="status">
            <h4>This choice needs another look</h4>
            <ul>
              @for (line of staleLines(); track line) {
                <li>{{ line }}</li>
              }
            </ul>
            <p>Nothing was changed. The choice stands until someone changes it.</p>
            @if (canKeep()) {
              <button
                type="button"
                [disabled]="blocked()"
                [attr.aria-describedby]="named() ? null : hintId"
                (click)="keep.emit()"
              >
                {{ current.status === 'none' ? 'Keep “none of these”' : 'Keep this choice' }}
              </button>
            }
            @if (goneCandidate(); as gone) {
              <div class="gone">
                <span class="mono">{{ gone.ktype }}</span> <b>{{ gone.model }}</b>
                <span class="tag">chosen · no longer a candidate</span>
                <div class="muted">{{ describe(gone) }}</div>
              </div>
            }
          </div>
        }
      }
    }

    <div class="fields">
      <label>
        Your name
        <input
          type="text"
          required
          maxlength="120"
          autocomplete="name"
          [value]="reviewer()"
          (input)="reviewer.set(typed($event))"
        />
      </label>
      <label>
        Reason (optional)
        <textarea
          rows="1"
          maxlength="1000"
          [value]="reason()"
          (input)="reason.set(typed($event))"
        ></textarea>
      </label>
    </div>
    @if (!named()) {
      <p class="muted hint" [id]="hintId">Enter your name to record a choice.</p>
    }

    <ng-content />

    <div class="below">
      <button
        type="button"
        [disabled]="blocked()"
        [attr.aria-describedby]="named() ? null : hintId"
        (click)="none.emit()"
      >
        {{ noCandidates() ? 'Record that no KType fits' : 'None of these' }}
      </button>
    </div>

    @if (pending(); as waiting) {
      @if (waiting.needsConfirm && !error()) {
        <div class="confirm" role="group" aria-label="Confirm the choice">
          <div class="lines" [id]="confirmLinesId">
            @for (line of confirmLines(); track line) {
              <p>{{ line }}</p>
            }
          </div>
          <button
            #confirmButton
            type="button"
            [disabled]="saving()"
            [attr.aria-busy]="saving()"
            [attr.aria-describedby]="confirmLinesId"
            (click)="confirm.emit()"
          >
            Confirm
          </button>
          <button type="button" [disabled]="saving()" (click)="cancel.emit()">Cancel</button>
        </div>
      }
    }

    @if (saving()) {
      <p class="muted" role="status">Saving…</p>
    } @else if (error(); as failure) {
      <p class="error" role="alert">
        {{ failure.message }}
        @if (failure.retry) {
          <button type="button" (click)="retry.emit()">Try again</button>
        }
      </p>
    } @else if (notice(); as text) {
      <p class="saved" role="status">{{ text }}</p>
    }

    @if (choice(); as current) {
      <details (toggle)="onHistoryToggle($event)">
        <summary>Choice history ({{ current.history_count }})</summary>
        @if (history(); as past) {
          <ol>
            @for (entry of past.entries; track entry.choice_id) {
              <li>
                {{ entry.created_at | date: 'yyyy-MM-dd HH:mm' }} · {{ entry.reviewer }} {{ did(entry) }}
                @if (entry.reason) {
                  — “{{ entry.reason }}”
                }
                <span class="muted">· matcher then: {{ entry.automatic_terminal.replace('_', ' ') }}</span>
              </li>
            }
          </ol>
        } @else if (historyError()) {
          <p class="error" role="alert">
            The history could not be loaded.
            <button type="button" (click)="historyOpened.emit()">Try again</button>
          </p>
        } @else {
          <p class="muted">Loading…</p>
        }
      </details>
    }
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 0.5rem; }
    p, h4 { margin: 0; }
    h4 { font-size: inherit; }
    .banner, .stale, .confirm {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 0.3rem;
      padding: 0.4rem 0.6rem;
      border-left: 3px solid #1f4d85;
      background: #eef3fb;
    }
    .banner--muted { border-left-color: var(--p-surface-300); background: var(--p-surface-50); color: var(--p-text-muted-color); }
    .stale { border-left-color: #c0392b; background: #fdf1f0; }
    .stale ul, ol { margin: 0; padding-left: 1.1rem; }
    .confirm { border-left-color: #d99a00; background: #fffaf0; flex-direction: row; flex-wrap: wrap; }
    .confirm .lines { flex-basis: 100%; }
    .quote { font-style: italic; }
    .gone { border: 1px dashed var(--p-surface-300); border-radius: 6px; padding: 0.3rem 0.5rem; }
    .tag { font-size: 0.66rem; padding: 0.05rem 0.35rem; border-radius: 999px; background: #fde4e4; color: #8a2020; }
    .fields { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: 0.5rem; }
    label { display: flex; flex-direction: column; gap: 0.15rem; font-weight: 600; }
    input, textarea { font: inherit; font-weight: 400; padding: 0.2rem 0.35rem; resize: vertical; }
    button { font: inherit; cursor: pointer; }
    button:disabled { cursor: not-allowed; }
    summary { cursor: pointer; font-weight: 600; }
    .hint { font-size: 0.72rem; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { color: #8a2020; }
    .saved { color: #1c5a2e; }
  `,
})
export class KTypeChoice {
  readonly lookup = input.required<VehicleMatchLookup>();
  readonly pending = input<PendingChoice | null>(null);
  readonly saving = input(false);
  readonly error = input<ChoiceError | null>(null);
  readonly notice = input<string | null>(null);
  readonly history = input<KTypeChoiceHistory | null>(null);
  readonly historyError = input(false);

  readonly reviewer = model('');
  readonly reason = model('');

  readonly confirm = output<void>();
  readonly cancel = output<void>();
  readonly none = output<void>();
  readonly withdraw = output<void>();
  readonly keep = output<void>();
  readonly retry = output<void>();
  readonly historyOpened = output<void>();
  readonly historyClosed = output<void>();

  protected readonly hintId = NAME_HINT_ID;
  protected readonly confirmLinesId = CONFIRM_LINES_ID;
  private readonly confirmButton = viewChild<ElementRef<HTMLButtonElement>>('confirmButton');

  constructor() {
    // The confirm step may open far from the button that asked for it: move focus there.
    effect(() => this.confirmButton()?.nativeElement.focus());
  }

  protected readonly choice = computed(() => this.lookup().choice ?? null);
  protected readonly named = computed(() => this.reviewer().trim().length > 0);
  protected readonly blocked = computed(() => !this.named() || this.saving());
  protected readonly noCandidates = computed(
    () => !this.lookup().candidates.length || this.lookup().bucket === 'not_matchable',
  );

  private isCandidate(ktype: string | null): boolean {
    return this.lookup().candidates.some((candidate) => candidate.ktype === ktype);
  }

  /** "Keep" re-records the same choice on today's evidence, so the KType must still be offered. */
  protected readonly canKeep = computed(() => {
    const current = this.choice();
    if (!current) return false;
    return current.status === 'none' || (current.status === 'chosen' && this.isCandidate(current.ktype));
  });

  protected readonly goneCandidate = computed(() => {
    const current = this.choice();
    if (current?.status !== 'chosen' || this.isCandidate(current.ktype)) return null;
    return current.chosen_candidate;
  });

  protected readonly staleLines = computed(() => {
    const current = this.choice();
    if (!current) return [];
    const lines: string[] = [];
    for (const reason of current.stale_reasons) {
      if (reason === 'catalog_batch_changed') {
        lines.push(
          `The TecDoc catalog changed since the choice (was ${current.catalog_batch}, now ${this.lookup().catalog_batch}).`,
        );
      } else if (reason === 'evidence_changed') {
        if (!current.changed_inputs.length) lines.push("The car's data changed since the choice.");
        for (const change of current.changed_inputs) {
          lines.push(
            `The car's data changed since the choice: ${fieldName(change.field)} ${show(change.then)} → ${show(change.now)}.`,
          );
        }
      } else if (reason === 'ktype_not_a_candidate') {
        lines.push(`KType ${current.ktype} is no longer among this car's candidates.`);
      } else if (reason === 'ktype_not_in_catalog') {
        lines.push(`KType ${current.ktype} is no longer in the TecDoc catalog.`);
      } else if (reason === 'new_candidates') {
        lines.push('KTypes are offered now that were not offered when “none of these” was recorded.');
      } else {
        lines.push(String(reason));
      }
    }
    return lines;
  });

  /** What the person is about to do, one line per thing worth a second thought. */
  protected readonly confirmLines = computed(() => {
    const body = this.pending()?.body;
    if (!body) return [];
    const result = this.lookup();
    const current = this.choice();
    if (body.action === 'withdraw') return ['Withdraw the choice? The automatic result will apply again.'];
    if (body.action === 'none') return ['Record that none of these KTypes is this car?'];
    const lines: string[] = [];
    const candidate = result.candidates.find((item) => item.ktype === body.ktype);
    if (result.terminal === 'resolved' && result.top_ktype && result.top_ktype !== body.ktype) {
      lines.push(
        `The matcher resolved this car to KType ${result.top_ktype}. Your choice of KType ${body.ktype} will be shown instead; the matcher's result stays visible.`,
      );
    }
    if (candidate && !candidate.compatible) {
      const fields = candidate.conflicting_fields.map(fieldName).join(', ');
      lines.push(`KType ${body.ktype} conflicts with this car on ${fields}. Choose it anyway?`);
    }
    if (current?.status === 'chosen') {
      lines.push(`Replace ${current.reviewer}'s choice of KType ${current.ktype} with KType ${body.ktype}?`);
    } else if (current?.status === 'none') {
      lines.push(`Replace ${current.reviewer}'s “none of these” with KType ${body.ktype}?`);
    }
    return lines.length ? lines : [`Choose KType ${body.ktype} for this car?`];
  });

  protected did(entry: KTypeChoiceHistoryEntry): string {
    if (entry.action === 'choose') return `chose KType ${entry.ktype}`;
    return entry.action === 'none' ? 'recorded none of these' : 'withdrew';
  }

  protected describe(candidate: KTypeCandidate): string {
    const years =
      candidate.year_from === null && candidate.year_to === null
        ? null
        : `${candidate.year_from ?? '…'}–${candidate.year_to ?? 'now'}`;
    return [
      years,
      candidate.power_kw !== null ? `${candidate.power_kw} kW` : null,
      candidate.displacement_cc !== null ? `${candidate.displacement_cc} cc` : null,
      candidate.engine_codes.join(' / '),
      candidate.fuels.join(', '),
      candidate.drive_type,
      candidate.bodyworks.join(', '),
    ]
      .filter(Boolean)
      .join(' · ');
  }

  protected onHistoryToggle(event: Event): void {
    if ((event.target as HTMLDetailsElement).open) this.historyOpened.emit();
    else this.historyClosed.emit();
  }

  /** The text of the input or textarea an `input` event came from. */
  protected typed(event: Event): string {
    return (event.target as HTMLInputElement | HTMLTextAreaElement).value;
  }
}
