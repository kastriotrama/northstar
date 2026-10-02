import { DOCUMENT, DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  model,
  output,
  signal,
  viewChild,
  viewChildren,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, catchError, of, switchMap } from 'rxjs';

import { Api } from '../core/api';
import { fieldName } from '../core/match-reasons';
import type {
  CorrectableField,
  FactCorrectionAction,
  FactCorrectionHistory,
  FactCorrectionHistoryEntry,
  FactCorrectionRequest,
  FactCorrectionState,
  VehicleMatchLookup,
} from '../core/models';

/** The name is typed in the choice panel's field; a saved correction remembers it under its key. */
const REVIEWER_STORAGE_KEY = 'match-review-reviewer';

/** Where the value the matcher uses came from, in plain words. */
const SOURCE_NOTES: Record<string, string> = {
  registry: 'from the registry',
  ais: 'from AIS',
  rule: 'filled by a rule',
  review: 'set by a reviewer rule',
  derived: "worked out from the car's other data",
  correction: 'corrected by a person',
};

/** How a field is involved in what blocks the car, the most telling first. */
const HINTS = {
  every: 'conflicts with every candidate',
  gap: 'candidates differ on this and the car has no value',
  some: 'conflicts with some candidates',
  differ: 'candidates differ on this',
  flagged: 'the matcher flagged this value',
  uncompared: 'the matcher could not compare this',
} as const;

type Hint = keyof typeof HINTS;

const HINT_ORDER = Object.keys(HINTS) as Hint[];

/** A list (the fuels of a car) holds at least one value and at most this many. */
const LIST_MAX = 3;

/**
 * Not a value of the car: its record was stopped before matching, and a person may release
 * it ("match this car anyway"). Recorded as a correction of this field, so it is not a row.
 */
const STOP_FIELD = 'normalization_stop';

/** Why a record was stopped before matching, in plain words; any other code reads with spaces. */
const STOP_REASONS: Record<string, string> = {
  tyre_size_unrecognized: 'tyre size could not be read',
  type_approval_format_unrecognized: 'type approval number could not be read',
  manufacturer_missing: 'no manufacturer',
  normalization_review_required: 'sent to review without a stated reason',
};

const DATA_CHANGED = "This car's data or matching changed since you opened it.";

/**
 * Refusals that mean the screen is out of date: the car is read again and handed on.
 * Each is said for a value and for a release.
 */
const OUT_OF_DATE: Record<string, readonly [value: string, release: string]> = {
  correction_changed: [
    'Someone else corrected this value while you were looking.',
    "Someone else changed this car's release while you were looking.",
  ],
  evidence_changed: [DATA_CHANGED, DATA_CHANGED],
  nothing_to_ignore: [
    'This car has no value here to mark as wrong.',
    'This car is no longer stopped before matching.',
  ],
  nothing_to_withdraw: ['There is no correction to undo.', 'There is no release to undo.'],
};
const NOW_SHOWN = 'What is in force now is shown.';
const NOT_READ = 'The car could not be read again: close it and open it once more.';

/** The other refusals, in plain words. `{label}` is the field's label. */
const REFUSALS: Record<string, string> = {
  value_unchanged: 'Not saved. That is the value this car already has.',
  invalid_value: 'Not saved. That is not a valid value for {label}.',
  field_not_correctable: 'Not saved. {label} cannot be corrected here.',
  reason_required: 'Not saved. Give a reason to match this car anyway.',
  vehicle_not_found: 'Not saved. This car is no longer in NorthStar.',
  operation_id_reused: 'Not saved. Please do it once more.',
};

const NOT_ACCEPTED = 'The server did not accept this request.';

/** What a refused request body is about, by the field FastAPI names. */
const BODY_FIELDS: Record<string, string> = {
  reviewer: 'your name',
  reason: 'the reason',
  value: 'the value',
};

interface ApiError {
  status?: number;
  error?: { detail?: unknown };
}

/** FastAPI's own check of the request body answers with a list: say the first thing it names. */
function validationMessage(detail: readonly unknown[]): string {
  for (const entry of detail) {
    if (!entry || typeof entry !== 'object') continue;
    const { loc, msg } = entry as { loc?: unknown; msg?: unknown };
    const key = Array.isArray(loc) ? String(loc[loc.length - 1]) : '';
    const subject = BODY_FIELDS[key];
    if (!subject) continue;
    const said = typeof msg === 'string' ? msg.replace(/^Value error, /, '') : '';
    // The API's own checks start with the field: "reviewer must be 1 to 120 characters".
    return said.startsWith(`${key} `)
      ? `${subject[0].toUpperCase()}${subject.slice(1)}${said.slice(key.length)}.`
      : `The server did not accept ${subject}.`;
  }
  return NOT_ACCEPTED;
}

/** `detail` is `{code, message}` on the correction endpoints, a list or a string elsewhere. */
function errorDetail(err: ApiError): { code: string | null; message: string | null } {
  const detail = err?.error?.detail;
  if (typeof detail === 'string') return { code: null, message: detail };
  if (Array.isArray(detail)) return { code: null, message: validationMessage(detail) };
  if (detail && typeof detail === 'object') {
    const { code, message } = detail as { code?: unknown; message?: unknown };
    return {
      code: typeof code === 'string' ? code : null,
      message: typeof message === 'string' ? message : null,
    };
  }
  return { code: null, message: null };
}

/** A correction on its way to the server; "Try again" resends it unchanged. */
interface PendingCorrection {
  vehicleId: string;
  /** What is said once it is saved, bar what matching the car again gave. */
  done: string;
  label: string;
  /** Carries the operation id, minted once per action. */
  body: FactCorrectionRequest;
}

interface CorrectionError {
  message: string;
  /** The same operation can be sent again (network or server trouble). */
  retry: boolean;
}

/** What an action is recorded against: a row's field, or the stop before matching. */
interface Target {
  field: string;
  /** Names it in what is said about a refusal. */
  label: string;
  /** The head of its chain, a withdrawn one too: the next action supersedes it. */
  head: FactCorrectionState | null;
}

/** The control a field is corrected with, read off its type and vocabulary, never its name. */
type Editor = 'text' | 'number' | 'choice' | 'list';

interface Row extends Target {
  editor: Editor;
  /** The closed vocabulary; empty when any value is accepted. */
  values: string[];
  /** A whole number whose allowed values are an unbroken run (a month, 1 to 12): its ends. */
  range: { min: number; max: number } | null;
  suggestions: string[];
  /** What the matcher uses today; null when the car has no value or its value is ignored. */
  value: string | null;
  source: string | null;
  hint: Hint | null;
  /** The head when it is in force (`set` or `ignored`). */
  standing: FactCorrectionState | null;
  withdrawn: FactCorrectionState | null;
}

/** One recorded action, said in words. */
interface HistoryLine extends FactCorrectionHistoryEntry {
  did: string;
}

/** A matcher key or reason code belongs to a field by name or as a prefix (`engine_code_unverified`). */
function touches(keys: readonly string[], own: readonly string[]): boolean {
  return keys.some((key) => own.some((name) => key === name || key.startsWith(`${name}_`)));
}

function hintFor(field: CorrectableField, result: VehicleMatchLookup): Hint | null {
  const own = field.evidence_keys;
  const conflicts = result.candidates.filter((candidate) =>
    touches(candidate.conflicting_fields, own),
  ).length;
  if (conflicts > 0 && conflicts === result.candidates.length) return 'every';
  const differ =
    touches(result.separating_fields, own) || touches(result.missing_separating_fields, own);
  if (differ && field.current_value === null) return 'gap';
  if (conflicts > 0) return 'some';
  if (differ) return 'differ';
  if (touches(result.reason_codes, own)) return 'flagged';
  const uncompared = result.candidates.some((candidate) => touches(candidate.missing_fields, own));
  return uncompared ? 'uncompared' : null;
}

function editorFor(field: CorrectableField, range: Row['range']): Editor {
  if (field.type === 'list') return 'list';
  if (field.values.length && !range) return 'choice';
  return field.type === 'integer' ? 'number' : 'text';
}

/** The ends of a whole-number vocabulary without gaps, so it can be typed rather than picked. */
function rangeOf(field: CorrectableField): Row['range'] {
  if (field.type !== 'integer' || field.values.length < 2) return null;
  const numbers = field.values.map(Number);
  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const unbroken = numbers.every(Number.isInteger) && new Set(numbers).size === max - min + 1;
  return unbroken ? { min, max } : null;
}

/** Whether a suggested value is one the field's vocabulary takes; an open field takes any. */
function accepts(field: CorrectableField, value: string): boolean {
  if (!field.values.length) return true;
  if (field.type !== 'list') return field.values.includes(value);
  const picks = value.split(',');
  return picks.length <= LIST_MAX && picks.every((pick) => field.values.includes(pick));
}

/** A value as it would be sent: a list's picks in the vocabulary's order, anything else trimmed. */
function entered(row: Pick<Row, 'editor' | 'values'>, text: string): string {
  if (row.editor !== 'list') return text.trim();
  const picks = text.split(',');
  return row.values.filter((value) => picks.includes(value)).join(',');
}

/** A value as the screen says it: a vocabulary's words without underscores, a list with commas. */
function inWords(row: Pick<Row, 'editor'> | undefined, value: string | null): string {
  if (value === null || value === '') return '—';
  const worded = row?.editor === 'choice' || row?.editor === 'list';
  return worded ? value.replace(/_/g, ' ').replace(/,/g, ', ') : value;
}

/** What matching the car again gave, for the line that says a correction was saved. */
function outcome(result: VehicleMatchLookup): string {
  if (result.bucket === 'not_matchable') {
    return 'The car was checked again and cannot be matched as it is.';
  }
  const fits = result.candidates.filter((candidate) => candidate.compatible).length;
  const count = fits === 0 ? 'no KType fits' : fits === 1 ? 'one KType fits' : `${fits} KTypes fit`;
  return `The car was matched again: ${count}.`;
}

let instances = 0;

/**
 * A person's corrections to the data the matcher reads for one car: set a value, mark the
 * present one as wrong, or undo a correction -- and release a car that was stopped before
 * matching. Each one is recorded for this car only and the car is matched again at once:
 * a correction is evidence, never a bypass.
 *
 * Sends its own requests. The name and reason are the ones typed in the choice panel's fields,
 * and the container shows the lookup `corrected` carries. Which fields there are, and how each is
 * edited, comes from the lookup: a field the server adds needs no change here.
 */
@Component({
  selector: 'ns-fact-corrections',
  imports: [DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[hidden]': '!shown()' },
  template: `
    @if (shown()) {
      <h4>Correct this car's data</h4>
      <p class="muted small">For this car only. The car is matched again after each change.</p>
      @if (!named()) {
        <p class="muted small" [id]="hintId">Enter your name to correct this car's data.</p>
      }

      @if (stopped(); as reasons) {
        <div #stopBlock class="stop" tabindex="-1" role="group" aria-label="Stop before matching">
          @if (released(); as release) {
            <p>
              Released for matching by {{ release.reviewer }} on
              {{ release.created_at | date: 'yyyy-MM-dd' }}{{ release.reason ? ': “' + release.reason + '”' : '' }}
              (was stopped for: {{ reasons }})
            </p>
            <button
              #stopButton
              type="button"
              [disabled]="blocked()"
              [attr.aria-describedby]="named() ? null : hintId"
              (click)="undoRelease()"
            >
              Undo release
            </button>
          } @else {
            <p>
              This car was stopped before matching: {{ reasons }}. It has not been compared with any
              KType.
            </p>
            @if (unreleased(); as undone) {
              <p class="muted small">
                An earlier release was undone by {{ undone.reviewer }} on
                {{ undone.created_at | date: 'yyyy-MM-dd' }}.
              </p>
            }
            @if (!reasoned()) {
              <p class="muted small" [id]="reasonHintId">Give a reason to match this car anyway.</p>
            }
            <button
              #stopButton
              type="button"
              [disabled]="!canRelease()"
              [attr.aria-expanded]="confirming()"
              [attr.aria-describedby]="releaseHints()"
              (click)="askRelease()"
            >
              Match this car anyway
            </button>
            @if (confirming()) {
              <div class="line" role="group" aria-label="Confirm matching this car anyway">
                <p>
                  Match this car even though it was stopped for: {{ reasons }}? The matcher's checks
                  still apply.
                </p>
                <button
                  #confirm
                  type="button"
                  [disabled]="!canRelease()"
                  aria-label="Confirm: match this car anyway"
                  (click)="release()"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  aria-label="Cancel: match this car anyway"
                  (click)="cancelRelease(stopButton)"
                >
                  Cancel
                </button>
              </div>
            }
          }
          @if (acted() === stopField) {
            @if (saving()) {
              <p class="muted" role="status">Saving and matching the car again…</p>
            } @else if (error(); as failure) {
              <p class="error" role="alert">
                {{ failure.message }}
                @if (failure.retry) {
                  <button type="button" aria-label="Try again: the release" (click)="send()">
                    Try again
                  </button>
                }
              </p>
            } @else if (notice(); as text) {
              <p class="saved" role="status">{{ text }}</p>
            }
          }
        </div>
      }

      <ul>
        @for (row of rows(); track row.field) {
          <li>
            <div class="row" [class.row--made]="!!row.standing">
              <span class="muted">{{ row.label }}</span>
              <span class="muted">
                <b>{{ say(row, row.value) }}</b>
                @if (row.standing; as made) {
                  @if (made.status === 'set') {
                    corrected by {{ made.reviewer }} on {{ made.created_at | date: 'yyyy-MM-dd' }}:
                    {{ say(row, made.previous_value) }} → {{ say(row, made.value) }}
                  } @else {
                    marked wrong by {{ made.reviewer }} on
                    {{ made.created_at | date: 'yyyy-MM-dd' }} (was {{ say(row, made.previous_value) }})
                  }
                  @if (made.reason) {
                    — “{{ made.reason }}”
                  }
                } @else {
                  {{ row.source }}
                  @if (row.withdrawn; as undone) {
                    @if (row.source) {
                      ·
                    }
                    an earlier correction was withdrawn by {{ undone.reviewer }} on
                    {{ undone.created_at | date: 'yyyy-MM-dd' }}
                  }
                }
                @if (row.hint; as hint) {
                  <span class="hint hint--{{ hint }}">{{ hints[hint] }}</span>
                }
              </span>
              <span class="buttons">
                <button
                  #opener
                  type="button"
                  [disabled]="blocked()"
                  [attr.data-field]="row.field"
                  [attr.aria-label]="'Correct ' + row.label"
                  [attr.aria-expanded]="editing() === row.field"
                  [attr.aria-describedby]="named() ? null : hintId"
                  (click)="toggle(row, opener)"
                >
                  Correct…
                </button>
                @if (row.standing) {
                  <button
                    type="button"
                    [disabled]="blocked()"
                    [attr.aria-label]="'Undo correction of ' + row.label"
                    [attr.aria-describedby]="named() ? null : hintId"
                    (click)="act(row, 'withdraw')"
                  >
                    Undo correction
                  </button>
                }
              </span>
            </div>

            @if (editing() === row.field) {
              <form
                class="editor"
                novalidate
                [attr.aria-label]="'Correct ' + row.label"
                (submit)="save($event, row)"
                (keydown.escape)="close(opener)"
              >
                @switch (row.editor) {
                  @case ('list') {
                    <fieldset>
                      <legend>New value <span class="plain">(choose 1 to {{ listMax }})</span></legend>
                      @for (value of row.values; track value) {
                        <label class="plain">
                          <input
                            #control
                            type="checkbox"
                            [checked]="picked(value)"
                            [disabled]="!picked(value) && full(row)"
                            (change)="onPick(row, value, $event)"
                          />
                          {{ say(row, value) }}
                        </label>
                      }
                    </fieldset>
                  }
                  @case ('choice') {
                    <label>
                      New value
                      <select #control (change)="onDraft($event)">
                        <option value="" disabled [selected]="!row.values.includes(draft())">
                          Choose…
                        </option>
                        @for (value of row.values; track value) {
                          <option [value]="value" [selected]="draft() === value">
                            {{ say(row, value) }}
                          </option>
                        }
                      </select>
                    </label>
                  }
                  @default {
                    <label>
                      New value
                      @if (row.range; as range) {
                        ({{ range.min }} to {{ range.max }})
                      }
                      <input
                        #control
                        type="text"
                        maxlength="80"
                        autocomplete="off"
                        [attr.inputmode]="row.editor === 'number' ? 'numeric' : null"
                        [value]="draft()"
                        (input)="onDraft($event)"
                      />
                    </label>
                  }
                }
                @if (row.suggestions.length) {
                  <div class="line" role="group" aria-label="Values the candidates have">
                    <span class="muted">The candidates have:</span>
                    @for (value of row.suggestions; track value) {
                      <button
                        type="button"
                        [disabled]="saving()"
                        [attr.aria-label]="'Use ' + say(row, value) + ' for ' + row.label"
                        (click)="draft.set(value)"
                      >
                        {{ say(row, value) }}
                      </button>
                    }
                  </div>
                }
                <div class="line">
                  <button
                    type="submit"
                    [disabled]="!canSave(row)"
                    [attr.aria-label]="'Save ' + row.label"
                    [attr.aria-busy]="saving()"
                  >
                    Save
                  </button>
                  @if (row.value !== null) {
                    <button
                      type="button"
                      [disabled]="blocked()"
                      [attr.aria-label]="'Mark the present value as wrong: ' + row.label"
                      (click)="act(row, 'ignore')"
                    >
                      Mark the present value as wrong
                    </button>
                  }
                  <button
                    type="button"
                    [disabled]="saving()"
                    [attr.aria-label]="'Cancel correcting ' + row.label"
                    (click)="close(opener)"
                  >
                    Cancel
                  </button>
                </div>
                @if (row.value !== null) {
                  <p class="muted small">
                    A value marked as wrong is no longer used to match this car. Do that when the
                    right value is not known.
                  </p>
                }
              </form>
            }

            @if (acted() === row.field) {
              @if (saving()) {
                <p class="muted" role="status">Saving and matching the car again…</p>
              } @else if (error(); as failure) {
                <p class="error" role="alert">
                  {{ failure.message }}
                  @if (failure.retry) {
                    <button type="button" [attr.aria-label]="'Try again: ' + row.label" (click)="send()">
                      Try again
                    </button>
                  }
                </p>
              } @else if (notice(); as text) {
                <p class="saved" role="status">{{ text }}</p>
              }
            }
          </li>
        }
      </ul>

      @if (recorded(); as count) {
        <details (toggle)="onHistoryToggle($event)">
          <summary>Correction history ({{ count }})</summary>
          @if (historyFailed()) {
            <p class="error" role="alert">
              The correction history could not be loaded.
              <button
                type="button"
                aria-label="Try again to load the correction history"
                (click)="loadHistory()"
              >
                Try again
              </button>
            </p>
          } @else if (past(); as lines) {
            <ol>
              @for (line of lines; track line.correction_id) {
                <li>
                  {{ line.created_at | date: 'yyyy-MM-dd HH:mm' }} · {{ line.reviewer }} {{ line.did }}
                  @if (line.reason) {
                    — “{{ line.reason }}”
                  }
                </li>
              }
            </ol>
          } @else {
            <p class="muted">Loading…</p>
          }
        </details>
      }
    }
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
      padding: 0.4rem 0.5rem;
      border: 1px solid var(--p-surface-200);
      border-radius: 6px;
    }
    :host([hidden]) { display: none; }
    h4, p, ul, ol { margin: 0; }
    h4 { font-size: inherit; }
    ul { padding: 0; list-style: none; }
    ul:empty { display: none; }
    ol { padding-left: 1.1rem; }
    ul > li {
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
      padding: 0.25rem 0;
      border-top: 1px solid var(--p-surface-100);
    }
    .row {
      display: grid;
      grid-template-columns: minmax(80px, 28%) minmax(0, 1fr) auto;
      gap: 0.5rem;
      align-items: start;
      overflow-wrap: anywhere;
    }
    .row b { color: var(--p-text-color); }
    .buttons { display: flex; gap: 0.3rem; }
    /* A corrected row says more: its buttons go under the words rather than beside them. */
    .row--made .buttons { grid-column: 2 / -1; }
    .hint { display: block; color: #7a5300; }
    .hint--every, .hint--some { color: #8a2020; }
    .editor, .stop {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 0.35rem;
      padding: 0.4rem 0.6rem;
      border-left: 3px solid #1f4d85;
      background: #eef3fb;
    }
    .stop { border-left-color: #d99a00; background: #fffaf0; }
    .line, fieldset { display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem; }
    .line p { flex-basis: 100%; }
    fieldset { min-width: 0; margin: 0; padding: 0; border: 0; column-gap: 0.8rem; }
    label { display: flex; flex-direction: column; gap: 0.15rem; }
    label, legend { padding: 0; font-weight: 600; }
    label.plain { flex-direction: row; align-items: center; }
    .plain { font-weight: 400; }
    input, select { font: inherit; font-weight: 400; padding: 0.2rem 0.35rem; }
    button { font: inherit; cursor: pointer; }
    button:disabled { cursor: not-allowed; }
    summary { cursor: pointer; font-weight: 600; }
    .small { font-size: 0.72rem; }
    .muted { color: var(--p-text-muted-color); }
    .error { color: #8a2020; }
    .saved { color: #1c5a2e; }
  `,
})
export class FactCorrections {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);
  private readonly document = inject(DOCUMENT);

  /** The car's lookup: what may be corrected, what is corrected, and the evidence shown. */
  readonly lookup = input.required<VehicleMatchLookup>();
  /** The name typed in the choice panel's field. */
  readonly reviewer = input('');
  /**
   * The reason typed there. It belongs to one action: it is emptied once that action is
   * recorded, as the choice does, and kept when the action was refused.
   */
  readonly reason = model('');

  /**
   * The car's lookup after a correction was recorded and the car matched again -- or, after
   * a refusal that means the screen was out of date, the lookup as it is now.
   */
  readonly corrected = output<VehicleMatchLookup>();

  protected readonly hints = HINTS;
  protected readonly listMax = LIST_MAX;
  protected readonly stopField = STOP_FIELD;
  private readonly uid = `fact-corrections-${instances++}`;
  protected readonly hintId = `${this.uid}-name-hint`;
  protected readonly reasonHintId = `${this.uid}-reason-hint`;

  /** The field whose editor is open, and what is typed or picked in it (a list comma-joined). */
  protected readonly editing = signal<string | null>(null);
  protected readonly draft = signal('');
  protected readonly pending = signal<PendingCorrection | null>(null);
  protected readonly saving = signal(false);
  protected readonly error = signal<CorrectionError | null>(null);
  protected readonly notice = signal<string | null>(null);
  /** The field the last action was on: its row says saving, saved or what went wrong. */
  protected readonly acted = signal<string | null>(null);
  /** "Match this car anyway" was pressed and waits for its "Confirm". */
  protected readonly confirming = signal(false);

  private readonly history = signal<FactCorrectionHistory | null>(null);
  protected readonly historyFailed = signal(false);
  private historyOpen = false;
  private readonly historyAsked = new Subject<string>();

  private readonly control = viewChild<ElementRef<HTMLInputElement | HTMLSelectElement>>('control');
  private readonly openers = viewChildren<ElementRef<HTMLButtonElement>>('opener');
  private readonly confirmButton = viewChild<ElementRef<HTMLButtonElement>>('confirm');
  private readonly stopButton = viewChild<ElementRef<HTMLButtonElement>>('stopButton');
  private readonly stopBlock = viewChild<ElementRef<HTMLElement>>('stopBlock');

  constructor() {
    // The editor opens under the row, the confirm step under its button: move focus there.
    effect(() => this.control()?.nativeElement.focus());
    effect(() => this.confirmButton()?.nativeElement.focus());

    // A newer read of the history replaces one still on its way.
    this.historyAsked
      .pipe(
        switchMap((vehicleId) =>
          this.api.correctionHistory(vehicleId).pipe(catchError(() => of(null))),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((loaded) => {
        this.history.set(loaded);
        this.historyFailed.set(!loaded);
      });
  }

  protected readonly named = computed(() => this.reviewer().trim().length > 0);
  protected readonly blocked = computed(() => !this.named() || this.saving());

  /** Why the car's record was stopped before matching, in plain words; empty when it was not. */
  protected readonly stopped = computed(() =>
    (this.lookup().stop_reasons ?? [])
      .map((code) => STOP_REASONS[code] ?? code.replace(/_/g, ' '))
      .join(', '),
  );
  /** The stop as something to record against: its chain's head is the release, an undone one too. */
  private readonly stop = computed<Target>(() => ({
    field: STOP_FIELD,
    label: 'The release',
    head: (this.lookup().corrections ?? []).find((head) => head.field === STOP_FIELD) ?? null,
  }));
  protected readonly released = computed(() => {
    const head = this.stop().head;
    return head?.status === 'ignored' ? head : null;
  });
  protected readonly unreleased = computed(() => {
    const head = this.stop().head;
    return head?.status === 'withdrawn' ? head : null;
  });
  /** A release needs a reason as well as a name: it lets a car through that was stopped. */
  protected readonly reasoned = computed(() => this.reason().trim().length > 0);
  protected readonly canRelease = computed(() => !this.blocked() && this.reasoned());
  protected readonly releaseHints = computed(
    () =>
      [this.named() ? '' : this.hintId, this.reasoned() ? '' : this.reasonHintId]
        .filter(Boolean)
        .join(' ') || null,
  );

  /** One row per correctable field; the fields behind the car's result come first. */
  protected readonly rows = computed<Row[]>(() => {
    const result = this.lookup();
    const heads = new Map((result.corrections ?? []).map((head) => [head.field, head]));
    const rank = (row: Row) => (row.hint ? HINT_ORDER.indexOf(row.hint) : HINT_ORDER.length);
    return (result.correctable_fields ?? [])
      .map((field): Row => {
        const head = heads.get(field.field) ?? null;
        const standing = head && head.status !== 'withdrawn' ? head : null;
        const source = field.current_value === null ? null : field.current_source;
        const range = rangeOf(field);
        const editor = editorFor(field, range);
        return {
          field: field.field,
          label: field.label,
          editor,
          values: field.values,
          range,
          suggestions: field.suggestions.filter((value) => accepts(field, value)),
          value: field.current_value,
          source: source ? (SOURCE_NOTES[source] ?? `from ${source}`) : null,
          hint: hintFor(field, result),
          head,
          standing,
          withdrawn: head && !standing ? head : null,
        };
      })
      .sort((a, b) => rank(a) - rank(b));
  });

  /** There is something to correct, or a stop to release. */
  protected readonly shown = computed(() => this.rows().length > 0 || this.stopped() !== '');

  /** How many corrections, undone ones too, were ever recorded for this car. */
  protected readonly recorded = computed(() =>
    (this.lookup().corrections ?? []).reduce((sum, head) => sum + head.history_count, 0),
  );

  /** Every recorded action on this car, the newest first. */
  protected readonly past = computed<HistoryLine[] | null>(() => {
    const loaded = this.history();
    if (!loaded) return null;
    const rows = new Map(this.rows().map((row) => [row.field, row]));
    return loaded.fields
      .flatMap((chain) => {
        const row = rows.get(chain.field);
        const label = row?.label ?? fieldName(chain.field);
        return chain.entries.map((entry): HistoryLine => {
          if (chain.field === STOP_FIELD) {
            const undone = entry.action === 'withdraw';
            return { ...entry, did: undone ? 'undid the release' : 'released the car for matching' };
          }
          const was = inWords(row, entry.previous_value);
          const did =
            entry.action === 'set'
              ? `set ${label} to ${inWords(row, entry.value)} (was ${was})`
              : entry.action === 'ignore'
                ? `marked ${label} as wrong (was ${was})`
                : `undid the correction of ${label}`;
          return { ...entry, did };
        });
      })
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  });

  protected say(row: Row, value: string | null): string {
    return inWords(row, value);
  }

  /** Open the row's editor on its present value, or close it when it is open. */
  protected toggle(row: Row, opener: HTMLButtonElement): void {
    if (this.editing() === row.field) {
      this.close(opener);
      return;
    }
    this.reset();
    this.draft.set(row.editor === 'list' ? entered(row, row.value ?? '') : (row.value ?? ''));
    this.editing.set(row.field);
  }

  /** Close the editor without saving; a failed save waiting for "Try again" is given up. */
  protected close(opener: HTMLButtonElement): void {
    if (this.saving()) return;
    this.editing.set(null);
    this.reset();
    opener.focus();
  }

  protected onDraft(event: Event): void {
    this.draft.set((event.target as HTMLInputElement | HTMLSelectElement).value);
  }

  protected picked(value: string): boolean {
    return this.draft().split(',').includes(value);
  }

  /** A list takes no more: the boxes not ticked are off until one is unticked. */
  protected full(row: Row): boolean {
    return entered(row, this.draft()).split(',').filter(Boolean).length >= LIST_MAX;
  }

  protected onPick(row: Row, value: string, event: Event): void {
    const picks = this.draft().split(',').filter((item) => item !== value);
    if ((event.target as HTMLInputElement).checked) picks.push(value);
    this.draft.set(entered(row, picks.join(',')));
  }

  /** Something is typed or picked, and it is not the value the car already has. */
  protected canSave(row: Row): boolean {
    const value = entered(row, this.draft());
    return !this.blocked() && value !== '' && value !== entered(row, row.value ?? '');
  }

  protected save(event: Event, row: Row): void {
    event.preventDefault();
    if (!this.canSave(row)) return;
    const value = entered(row, this.draft());
    if (row.editor === 'number') {
      const range = row.range;
      const fits = /^\d+$/.test(value) && (!range || (+value >= range.min && +value <= range.max));
      if (!fits) {
        this.reset();
        this.acted.set(row.field);
        this.error.set({
          message: range
            ? `Enter a whole number from ${range.min} to ${range.max}.`
            : 'Enter a whole number, using digits only.',
          retry: false,
        });
        return;
      }
    }
    this.act(row, 'set', value);
  }

  protected act(row: Row, action: FactCorrectionAction, value: string | null = null): void {
    const done =
      action === 'set'
        ? `Saved. ${row.label} is now ${inWords(row, value)}.`
        : action === 'ignore'
          ? `Saved. ${row.label} is marked as wrong and no longer used.`
          : `Correction undone. ${row.label} is back to the car's own data.`;
    this.record(row, action, value, done);
  }

  /** Asked about first: a release lets a car through that was stopped for a reason. */
  protected askRelease(): void {
    this.reset();
    this.confirming.update((open) => !open);
  }

  protected cancelRelease(asker: HTMLButtonElement): void {
    this.confirming.set(false);
    asker.focus();
  }

  protected release(): void {
    if (!this.canRelease()) return;
    this.record(this.stop(), 'ignore', null, 'Released for matching.');
  }

  protected undoRelease(): void {
    this.record(this.stop(), 'withdraw', null, 'Release undone.');
  }

  /** A new action, hence a new operation id. */
  private record(
    target: Target,
    action: FactCorrectionAction,
    value: string | null,
    done: string,
  ): void {
    const result = this.lookup();
    const reviewer = this.reviewer().trim();
    if (!result.vehicle_id || !reviewer || this.saving()) return;
    this.reset();
    // Whatever is recorded now, a release still waiting for "Confirm" was asked before it.
    this.confirming.set(false);
    this.acted.set(target.field);
    this.pending.set({
      vehicleId: result.vehicle_id,
      label: target.label,
      done,
      body: {
        operation_id: crypto.randomUUID(),
        field: target.field,
        action,
        value,
        reviewer,
        reason: this.reason().trim() || null,
        supersedes_correction_id: target.head?.correction_id ?? null,
        evidence_fingerprint: result.evidence_fingerprint ?? null,
      },
    });
    this.send();
  }

  /** Also "Try again": the same operation id and body, which the server answers as a replay. */
  protected send(): void {
    const waiting = this.pending();
    if (!waiting || this.saving()) return;
    const { vehicleId, label, done, body } = waiting;
    this.saving.set(true);
    this.error.set(null);
    this.api
      .recordCorrection(vehicleId, body)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (lookup) => {
          if (vehicleId !== this.lookup().vehicle_id) return;
          this.saving.set(false);
          this.pending.set(null);
          if (this.editing() === body.field) this.editing.set(null);
          this.storeReviewer(body.reviewer);
          this.reason.set('');
          this.notice.set(`${done} ${outcome(lookup)}`);
          this.handOn(lookup);
          this.refocus(body.field);
        },
        error: (err: ApiError) => {
          if (vehicleId !== this.lookup().vehicle_id) return;
          this.saving.set(false);
          const { code, message } = errorDetail(err);
          const stale = code ? OUT_OF_DATE[code]?.[body.field === STOP_FIELD ? 1 : 0] : undefined;
          const refusal = code ? REFUSALS[code] : undefined;
          const status = err?.status ?? 0;
          if (stale) {
            this.pending.set(null);
            this.reload(vehicleId, stale);
          } else if (refusal) {
            this.pending.set(null);
            // What a field accepts (a range, a closed list) is the server's to say.
            const why = code === 'invalid_value' && message ? `Not saved. ${message}` : refusal;
            this.error.set({ message: why.replace('{label}', label), retry: false });
          } else if (status === 0 || status >= 500) {
            const busy = code === 'vehicle_busy';
            this.error.set({
              message: busy
                ? 'Not saved. Someone else is changing this car right now. Try again.'
                : 'Not saved. Try again.',
              retry: true,
            });
          } else {
            this.pending.set(null);
            this.error.set({ message: `Not saved. ${message ?? NOT_ACCEPTED}`, retry: false });
          }
        },
      });
  }

  /** The screen was out of date: say so, read the car as it is now and hand it on. */
  private reload(vehicleId: string, stale: string): void {
    const shown: CorrectionError = { message: `${stale} ${NOW_SHOWN}`, retry: false };
    this.error.set(shown);
    this.api
      .matchLookup(vehicleId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (lookup) => {
          if (vehicleId === this.lookup().vehicle_id) this.handOn(lookup);
        },
        error: () => {
          // Nothing newer is on screen after all -- unless the person has moved on meanwhile.
          if (this.error() === shown) {
            this.error.set({ message: `${stale} ${NOT_READ}`, retry: false });
          }
        },
      });
  }

  /** Give the container the car's lookup as it is now; a history left open is read again. */
  private handOn(lookup: VehicleMatchLookup): void {
    this.corrected.emit(lookup);
    if (this.historyOpen) this.loadHistory();
  }

  protected onHistoryToggle(event: Event): void {
    this.historyOpen = (event.target as HTMLDetailsElement).open;
    if (this.historyOpen) this.loadHistory();
  }

  /** Read the history afresh: when it is opened, after a save while it is open, on "Try again". */
  protected loadHistory(): void {
    const vehicleId = this.lookup().vehicle_id;
    if (!vehicleId) return;
    this.historyFailed.set(false);
    this.historyAsked.next(vehicleId);
  }

  /**
   * The button that was pressed is gone once saved (the editor, "Undo correction", the confirm
   * step): put focus on the row's "Correct…", or on the stop's own button -- on its words when
   * that button waits for a reason, since a button that is off cannot take focus. Focus the
   * person has put elsewhere meanwhile is left where it is.
   */
  private refocus(field: string): void {
    afterNextRender(
      () => {
        const active = this.document.activeElement;
        if (active && active !== this.document.body) return;
        const opener = this.openers().find((item) => item.nativeElement.dataset['field'] === field);
        const button = this.stopButton()?.nativeElement;
        const stop = button && !button.disabled ? button : this.stopBlock()?.nativeElement;
        (field === STOP_FIELD ? stop : opener?.nativeElement)?.focus();
      },
      { injector: this.injector },
    );
  }

  private reset(): void {
    this.pending.set(null);
    this.error.set(null);
    this.notice.set(null);
    this.acted.set(null);
  }

  private storeReviewer(name: string): void {
    try {
      localStorage.setItem(REVIEWER_STORAGE_KEY, name);
    } catch {
      // A blocked storage only costs retyping the name.
    }
  }
}
