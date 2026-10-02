import { DOCUMENT, DatePipe, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  InjectionToken,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  model,
  output,
  signal,
  untracked,
  viewChild,
  viewChildren,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  type Observable,
  Subject,
  type Subscription,
  catchError,
  concat,
  map,
  of,
  repeat,
  retry,
  switchMap,
  takeWhile,
  throwError,
  timer,
} from 'rxjs';

import { Api } from '../core/api';
import { fieldName } from '../core/match-reasons';
import type {
  CarMatch,
  CorrectableField,
  CorrectionDecisionRef,
  CorrectionOutcome,
  CorrectionPreviewJob,
  CorrectionScopeChoice,
  CorrectionScopeOption,
  CorrectionScopesRequest,
  FactCorrectionAction,
  FactCorrectionHistory,
  FactCorrectionHistoryEntry,
  FactCorrectionRequest,
  FactCorrectionState,
  VehicleMatchLookup,
} from '../core/models';
import {
  type CorrectionCarList,
  type CorrectionCarLists,
  CorrectionPreview,
  appliedInWords,
  carCount,
  undoneInWords,
} from './correction-preview';

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
  not_storable: 'Not saved. This value cannot be stored.',
  reason_required: 'Not saved. Give a reason first.',
  vehicle_not_found: 'Not saved. This car is no longer in NorthStar.',
  operation_id_reused: 'Not saved. Please do it once more.',
  // A correction for several cars: its check, and what is done with the result.
  busy: 'Another check is running. Try again in a moment.',
  scope_too_broad: 'This group is too large to check. Narrow it and check again.',
  preview_expired: 'The check is too old. Check again.',
  preview_not_found: 'The check is no longer there. Check again.',
  preview_incomplete: 'Not saved. The check did not reach every car. Check again.',
  nothing_to_apply: 'Not saved. No car in this group would take the correction.',
  harms_more_than_it_fixes:
    'Not saved. This would harm at least as many cars as it fixes. Narrow the group, or save it as a proposal.',
};
/** A release without a reason, said as the hint under its button says it. */
const RELEASE_UNREASONED = 'Not saved. Give a reason to match this car anyway.';

/** Refusals worth sending again unchanged: someone else holds what the request needs. */
const BUSY: Record<string, string> = {
  vehicle_busy: 'Not saved. Someone else is changing this car right now. Try again.',
  vehicles_busy: 'Not saved. Someone else is changing some of these cars right now. Try again.',
};

const NOT_ACCEPTED = 'The server did not accept this request.';
const CHECK_LOST = 'The check could not be followed to its end. Check again.';
const CHECK_NOT_STARTED = 'The check could not be started.';
const SAVING = 'Saving and matching the car again…';
const PROPOSED = 'Saved as a proposal. No car was changed: it is to be measured on all cars first.';

/** Refusals of an apply that mean its check no longer holds: the person checks again. */
const CHECK_GONE: readonly string[] = ['preview_expired', 'preview_not_found', 'preview_incomplete'];

/**
 * How long things wait: a check of several cars is asked how far it is about once a second,
 * and the other cars are looked for once the value has stopped changing.
 */
export const CORRECTION_TIMING = new InjectionToken<{ pollMs: number; settleMs: number }>(
  'CORRECTION_TIMING',
  { providedIn: 'root', factory: () => ({ pollMs: 1000, settleMs: 400 }) },
);
/** How many checked cars of one kind a list shows. */
const CARS_LISTED = 50;

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

/**
 * The question a 409 `confirmation_required` asks before a correction takes the KType from
 * a car that resolves today, or gives it another. Null for any other refusal.
 */
function question(err: ApiError, what: string): string | null {
  const detail = err?.error?.detail as
    | { code?: unknown; before?: Partial<CarMatch> | null; after?: Partial<CarMatch> | null }
    | null
    | undefined;
  if (err?.status !== 409 || detail?.code !== 'confirmation_required') return null;
  const today = typeof detail.before?.ktype === 'string' ? ` to KType ${detail.before.ktype}` : '';
  const after = detail.after;
  const then =
    after?.terminal === 'resolved' && typeof after.ktype === 'string'
      ? `it would resolve to KType ${after.ktype}`
      : 'it would no longer resolve';
  return `This car is resolved${today} today. With ${what} ${then}.`;
}

/** What a correction puts in force, to follow "With": "Engine code D5204T3". */
function inForce(label: string, action: FactCorrectionAction, value: string, stop = false): string {
  if (stop) return action === 'withdraw' ? 'the release undone' : 'the car released';
  if (action === 'set') return `${label} ${value}`;
  return action === 'ignore' ? `${label} marked as wrong` : `the correction of ${label} undone`;
}

/** A correction on its way to the server; "Try again" resends it unchanged. */
interface PendingCorrection {
  vehicleId: string;
  /** What is said once it is saved, bar what matching the car again gave. */
  done: string;
  label: string;
  /** What it puts in force, to follow "With" when the server asks before saving. */
  what: string;
  /** Carries the operation id, minted once per action. */
  body: FactCorrectionRequest;
}

/** An apply, a proposal or an undo for several cars on its way; "Try again" sends it again. */
interface PendingDecision {
  vehicleId: string;
  /** The row it is said under. */
  field: string;
  label: string;
  reviewer: string;
  /** Said while it is on its way. */
  saying: string;
  /**
   * Cold: every subscription sends the same body, with the operation id minted once.
   * Gives what is said once it is done.
   */
  request: Observable<string>;
  /** Cars were written, so this car is read again; a proposal writes none. */
  writes: boolean;
}

/** The correction the open editor holds, to ask whom else it could apply to. */
interface ScopesAsk {
  vehicleId: string;
  wait: number;
  body: CorrectionScopesRequest;
}

/** A request that failed on its way, not one the server refused. */
function transient(err: ApiError): boolean {
  const status = err?.status ?? 0;
  return status === 0 || status >= 500;
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

/** What is wrong with a typed number, said to the person; null when it fits or is not a number. */
function misfit(row: Pick<Row, 'editor' | 'range'>, value: string): string | null {
  if (row.editor !== 'number') return null;
  const range = row.range;
  if (/^\d+$/.test(value) && (!range || (+value >= range.min && +value <= range.max))) return null;
  return range
    ? `Enter a whole number from ${range.min} to ${range.max}.`
    : 'Enter a whole number, using digits only.';
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
 * matching. Each one is recorded for this car and the car is matched again at once:
 * a correction is evidence, never a bypass.
 *
 * A value, or "marked as wrong", may also go to the other cars like this one. Nothing is
 * saved for them before a check of what it would change, car by car; `ns-correction-preview`
 * says that step, and this component sends its requests and holds its state.
 *
 * Sends its own requests. The name and reason are the ones typed in the choice panel's fields,
 * and the container shows the lookup `corrected` carries. Which fields there are, and how each is
 * edited, comes from the lookup: a field the server adds needs no change here.
 */
@Component({
  selector: 'ns-fact-corrections',
  imports: [DatePipe, NgTemplateOutlet, CorrectionPreview],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[hidden]': '!shown()' },
  template: `
    @if (shown()) {
      <h4>Correct this car's data</h4>
      <p class="muted small">
        For this car only, unless you choose other cars under “Apply to”. The car is matched again
        after each change.
      </p>
      @if (!named()) {
        <p class="muted small" [id]="hintId">Enter your name to correct this car's data.</p>
      }

      <!-- What became of the last action, under the row (or the stop) it was on. -->
      <ng-template #said>
        @if (saving()) {
          <p class="muted" role="status">{{ saying() }}</p>
        } @else if (asking(); as question) {
          <div class="ask" role="group" [attr.aria-label]="'Confirm saving ' + about()">
            <p [id]="askId">{{ question }}</p>
            <div class="line">
              <button
                #confirm
                type="button"
                [attr.aria-label]="'Save anyway: ' + about()"
                [attr.aria-describedby]="askId"
                (click)="saveAnyway()"
              >
                Save anyway
              </button>
              <button type="button" [attr.aria-label]="'Cancel saving ' + about()" (click)="keep()">
                Cancel
              </button>
            </div>
          </div>
        } @else if (error(); as failure) {
          <p class="error" role="alert">
            {{ failure.message }}
            @if (failure.retry) {
              <button type="button" [attr.aria-label]="'Try again: ' + about()" (click)="again()">
                Try again
              </button>
            }
          </p>
        } @else if (notice(); as text) {
          <p class="saved" role="status">{{ text }}</p>
        }
      </ng-template>

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
            <ng-container [ngTemplateOutlet]="said" />
          }
        </div>
      }

      <ul>
        @for (row of rows(); track row.field) {
          <li #item>
            <div class="row" [class.row--made]="!!row.standing">
              <span class="muted">{{ row.label }}</span>
              <span class="muted">
                <b>{{ say(row, row.value) }}</b>
                @if (row.standing; as made) {
                  @if (made.decision; as group) {
                    {{ made.status === 'set' ? 'set' : 'marked wrong' }} for
                    {{ cars(group.member_count) }} like this by {{ group.reviewer }}:
                    “{{ group.scope_label }}” on {{ made.created_at | date: 'yyyy-MM-dd' }} (was
                    {{ say(row, made.previous_value) }})
                  } @else if (made.status === 'set') {
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
                @if (row.standing; as made) {
                  <button
                    type="button"
                    [disabled]="blocked()"
                    [attr.aria-label]="(made.decision ? 'Undo for this car: ' : 'Undo correction of ') + row.label"
                    [attr.aria-describedby]="named() ? null : hintId"
                    (click)="act(row, 'withdraw')"
                  >
                    {{ made.decision ? 'Undo for this car' : 'Undo correction' }}
                  </button>
                  @if (made.decision; as group) {
                    @if (group.member_count > 1) {
                      <button
                        type="button"
                        data-all
                        [disabled]="blocked()"
                        [attr.aria-label]="'Undo for all ' + cars(group.member_count) + ': ' + row.label"
                        [attr.aria-expanded]="undoing() === row.field"
                        [attr.aria-describedby]="named() ? null : hintId"
                        (click)="askUndoAll(row)"
                      >
                        Undo for all {{ cars(group.member_count) }}…
                      </button>
                    }
                  }
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
                            [disabled]="fixed() || (!picked(value) && full(row))"
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
                      <select #control [disabled]="fixed()" (change)="onDraft($event)">
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
                        [disabled]="fixed()"
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
                        [disabled]="saving() || fixed()"
                        [attr.aria-label]="'Use ' + say(row, value) + ' for ' + row.label"
                        (click)="use(value)"
                      >
                        {{ say(row, value) }}
                      </button>
                    }
                  </div>
                }
                @if (marking()) {
                  <p>
                    The present value, {{ say(row, row.value) }}, will be marked as wrong and no
                    longer used.
                  </p>
                }
                @if (asked()) {
                  <ns-correction-preview
                    [label]="row.label"
                    [change]="change(row)"
                    [scopes]="scopes()"
                    [choice]="choice()"
                    [check]="check()"
                    [lists]="lists()"
                    [saving]="saving()"
                    [(reason)]="reason"
                    (choose)="choice.set($event)"
                    (lookAgain)="offer(true)"
                    (stop)="stopCheck()"
                    (seeCars)="listCars($event)"
                    (apply)="decide(row, 'apply', $event)"
                    (propose)="decide(row, 'propose', false)"
                    (narrow)="dropCheck()"
                    (cancel)="dropCheck(true)"
                  />
                }
                @if (!check()) {
                  <div class="line">
                    <button
                      #primary
                      type="submit"
                      [disabled]="!canSave(row)"
                      [attr.aria-label]="(choice() ? 'Check ' : 'Save ') + row.label"
                      [attr.aria-busy]="saving()"
                    >
                      {{ choice() ? 'Check' : 'Save' }}
                    </button>
                    @if (row.value !== null) {
                      <button
                        type="button"
                        [disabled]="blocked()"
                        [attr.aria-pressed]="marking()"
                        [attr.aria-label]="'Mark the present value as wrong: ' + row.label"
                        (click)="mark()"
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
                }
              </form>
            }

            @if (undoing() === row.field) {
              @if (row.standing?.decision; as group) {
                <div
                  class="ask"
                  role="group"
                  [attr.aria-label]="'Confirm undoing ' + row.label + ' for all ' + cars(group.member_count)"
                >
                  <p>
                    Undo this for all {{ cars(group.member_count) }}? Each goes back to its own data.
                    A car a person has changed since is left as it is.
                  </p>
                  <label>
                    Reason
                    <textarea
                      #undoReason
                      rows="1"
                      maxlength="1000"
                      [attr.aria-describedby]="reasoned() ? null : undoHintId"
                      [value]="reason()"
                      (input)="onReason($event)"
                    ></textarea>
                  </label>
                  @if (!reasoned()) {
                    <p class="muted small" [id]="undoHintId">
                      Give a reason to undo this for several cars.
                    </p>
                  }
                  <div class="line">
                    <button
                      type="button"
                      [disabled]="blocked() || !reasoned()"
                      [attr.aria-label]="'Confirm: undo ' + row.label + ' for all ' + cars(group.member_count)"
                      [attr.aria-describedby]="reasoned() ? null : undoHintId"
                      (click)="undoAll(row, group)"
                    >
                      Confirm
                    </button>
                    <button
                      type="button"
                      [attr.aria-label]="'Cancel: undo ' + row.label + ' for all ' + cars(group.member_count)"
                      (click)="cancelUndo(item)"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              }
            }

            @if (acted() === row.field) {
              <ng-container [ngTemplateOutlet]="said" />
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
    .editor, .stop, .ask {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 0.35rem;
      padding: 0.4rem 0.6rem;
      border-left: 3px solid #1f4d85;
      background: #eef3fb;
    }
    .stop, .ask { border-left-color: #d99a00; background: #fffaf0; }
    .line, fieldset { display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem; }
    .line p { flex-basis: 100%; }
    fieldset { min-width: 0; margin: 0; padding: 0; border: 0; column-gap: 0.8rem; }
    label { display: flex; flex-direction: column; gap: 0.15rem; }
    label, legend { padding: 0; font-weight: 600; }
    label.plain { flex-direction: row; align-items: center; }
    .plain { font-weight: 400; }
    input, select, textarea { font: inherit; font-weight: 400; padding: 0.2rem 0.35rem; }
    textarea { resize: vertical; }
    button { font: inherit; cursor: pointer; }
    button:disabled { cursor: not-allowed; }
    button[aria-pressed='true'] { font-weight: 600; }
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
  protected readonly askId = `${this.uid}-ask`;
  protected readonly undoHintId = `${this.uid}-undo-hint`;
  private readonly timing = inject(CORRECTION_TIMING);

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
  /** Said while a request is on its way. */
  protected readonly saying = signal(SAVING);
  /** The server asks before saving: the car is resolved today and would lose or change its KType. */
  protected readonly asking = signal<string | null>(null);

  /** "Mark the present value as wrong" is chosen in the open editor, rather than a new value. */
  protected readonly marking = signal(false);
  /** Whom the editor's correction could also apply to; null while asked, `failed` when it could not be. */
  protected readonly scopes = signal<CorrectionScopeOption[] | 'failed' | null>(null);
  /** The scope picked beyond this car; null is "Only this car". */
  protected readonly choice = signal<CorrectionScopeChoice | null>(null);
  /** The check of the picked scope: asked for, then the job as the server last reported it. */
  protected readonly check = signal<CorrectionPreviewJob | 'starting' | null>(null);
  protected readonly lists = signal<CorrectionCarLists>({});
  /** The field whose "Undo for all … cars…" waits for a reason and its "Confirm". */
  protected readonly undoing = signal<string | null>(null);
  private readonly decision = signal<PendingDecision | null>(null);
  private readonly scopesAsked = new Subject<ScopesAsk | null>();
  /** The running check: its start and the polls that follow it. */
  private checking: Subscription | null = null;

  private readonly history = signal<FactCorrectionHistory | null>(null);
  protected readonly historyFailed = signal(false);
  private historyOpen = false;
  private readonly historyAsked = new Subject<string>();

  private readonly control = viewChild<ElementRef<HTMLInputElement | HTMLSelectElement>>('control');
  private readonly openers = viewChildren<ElementRef<HTMLButtonElement>>('opener');
  private readonly confirmButton = viewChild<ElementRef<HTMLButtonElement>>('confirm');
  private readonly stopButton = viewChild<ElementRef<HTMLButtonElement>>('stopButton');
  private readonly stopBlock = viewChild<ElementRef<HTMLElement>>('stopBlock');
  private readonly primary = viewChild<ElementRef<HTMLButtonElement>>('primary');
  private readonly undoReason = viewChild<ElementRef<HTMLTextAreaElement>>('undoReason');

  constructor() {
    // The editor opens under the row, the confirm step under its button: move focus there.
    effect(() => this.control()?.nativeElement.focus());
    effect(() => this.confirmButton()?.nativeElement.focus());
    effect(() => this.undoReason()?.nativeElement.focus());

    // Another car: what was open, asked or being checked belonged to the one before.
    effect(() => {
      this.vehicleId();
      untracked(() => this.leave());
    });
    // A check still running when the panel goes is stopped: the server runs one at a time.
    this.destroyRef.onDestroy(() => this.endCheck());

    // The other cars are looked for once the value has stopped changing; a newer value
    // replaces a question still on its way.
    this.scopesAsked
      .pipe(
        switchMap((ask) =>
          ask
            ? timer(ask.wait).pipe(
                switchMap(() => this.api.correctionScopes(ask.vehicleId, ask.body)),
                map((answer): CorrectionScopeOption[] | 'failed' => answer.scopes),
                catchError(() => of('failed' as const)),
              )
            : of(null),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((offered) => this.scopes.set(offered));

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

  private readonly vehicleId = computed(() => this.lookup().vehicle_id);
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

  /** What the open editor would record: a new value that fits, or the present one marked as wrong. */
  private readonly intent = computed(() => {
    const row = this.rows().find((item) => item.field === this.editing());
    if (!row) return null;
    if (this.marking()) return row.value === null ? null : { action: 'ignore' as const, value: null };
    const value = entered(row, this.draft());
    if (value === '' || value === entered(row, row.value ?? '') || misfit(row, value)) return null;
    return { action: 'set' as const, value };
  });
  /** There is a correction to apply: whom it applies to is asked. */
  protected readonly asked = computed(() => {
    const result = this.lookup();
    return !!this.intent() && this.named() && !!result.vehicle_id && !!result.evidence_fingerprint;
  });
  /** A check is on screen: what it checked cannot be changed under it. */
  protected readonly fixed = computed(() => this.check() !== null);
  /** What "Try again" and the question before saving are about. */
  protected readonly about = computed(() => {
    const waiting = this.pending();
    if (waiting) return waiting.body.field === STOP_FIELD ? 'the release' : waiting.label;
    return this.decision()?.label ?? '';
  });

  protected say(row: Row, value: string | null): string {
    return inWords(row, value);
  }

  protected cars(count: number): string {
    return carCount(count);
  }

  /** What the open editor's correction puts in force: "Drive type fwd". */
  protected change(row: Row): string {
    const marking = this.marking();
    return inForce(row.label, marking ? 'ignore' : 'set', inWords(row, entered(row, this.draft())));
  }

  /** Open the row's editor on its present value, or close it when it is open. */
  protected toggle(row: Row, opener: HTMLButtonElement): void {
    if (this.editing() === row.field) {
      this.close(opener);
      return;
    }
    this.reset();
    this.shut();
    this.undoing.set(null);
    this.draft.set(row.editor === 'list' ? entered(row, row.value ?? '') : (row.value ?? ''));
    this.editing.set(row.field);
  }

  /** Close the editor without saving; a failed save waiting for "Try again" is given up. */
  protected close(opener: HTMLButtonElement): void {
    if (this.saving()) return;
    this.editing.set(null);
    this.reset();
    this.shut();
    opener.focus();
  }

  protected onDraft(event: Event): void {
    this.draft.set((event.target as HTMLInputElement | HTMLSelectElement).value);
    this.marking.set(false);
    this.offer();
  }

  /** A suggested value is taken as the new value. */
  protected use(value: string): void {
    this.draft.set(value);
    this.marking.set(false);
    this.offer();
  }

  /** "Mark the present value as wrong" is chosen, or given up again; "Save" or "Check" records it. */
  protected mark(): void {
    this.marking.update((on) => !on);
    this.offer();
  }

  protected onReason(event: Event): void {
    this.reason.set((event.target as HTMLTextAreaElement).value);
  }

  /**
   * The editor's correction changed: ask whom else it could apply to, starting from "Only this
   * car". `now` is "Try again", which does not wait for the value to settle.
   */
  protected offer(now = false): void {
    const result = this.lookup();
    const intent = this.intent();
    const field = this.editing();
    this.choice.set(null);
    this.scopes.set(null);
    this.scopesAsked.next(
      intent && field && result.vehicle_id && this.asked()
        ? {
            vehicleId: result.vehicle_id,
            wait: now ? 0 : this.timing.settleMs,
            body: { field, action: intent.action, value: intent.value },
          }
        : null,
    );
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
    this.marking.set(false);
    this.offer();
  }

  /**
   * Something is typed or picked, and it is not the value the car already has -- or the
   * present value is to be marked as wrong.
   */
  protected canSave(row: Row): boolean {
    if (this.blocked()) return false;
    if (this.marking()) return row.value !== null;
    const value = entered(row, this.draft());
    return value !== '' && value !== entered(row, row.value ?? '');
  }

  /** "Save" for this car alone; "Check" when the correction is to go to other cars too. */
  protected save(event: Event, row: Row): void {
    event.preventDefault();
    if (!this.canSave(row) || this.check()) return;
    const action = this.marking() ? 'ignore' : 'set';
    const value = action === 'set' ? entered(row, this.draft()) : null;
    const wrong = value === null ? null : misfit(row, value);
    if (wrong) {
      this.reset();
      this.acted.set(row.field);
      this.error.set({ message: wrong, retry: false });
      return;
    }
    const pick = this.choice();
    if (pick) this.startCheck(row, action, value, pick);
    else this.act(row, action, value);
  }

  protected act(row: Row, action: FactCorrectionAction, value: string | null = null): void {
    const done =
      action === 'set'
        ? `Saved. ${row.label} is now ${inWords(row, value)}.`
        : action === 'ignore'
          ? `Saved. ${row.label} is marked as wrong and no longer used.`
          : `Correction undone. ${row.label} is back to the car's own data.`;
    this.record(row, action, value, done, inWords(row, value));
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
    said = '',
  ): void {
    const result = this.lookup();
    const reviewer = this.reviewer().trim();
    if (!result.vehicle_id || !reviewer || this.saving()) return;
    this.reset();
    // Whatever is recorded now, a release still waiting for "Confirm" was asked before it.
    this.confirming.set(false);
    this.undoing.set(null);
    this.acted.set(target.field);
    this.pending.set({
      vehicleId: result.vehicle_id,
      label: target.label,
      what: inForce(target.label, action, said, target.field === STOP_FIELD),
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
    this.saying.set(SAVING);
    this.error.set(null);
    this.asking.set(null);
    this.api
      .recordCorrection(vehicleId, body)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (lookup) => {
          if (vehicleId !== this.lookup().vehicle_id) return;
          this.saving.set(false);
          this.pending.set(null);
          if (this.editing() === body.field) {
            this.editing.set(null);
            this.shut();
          }
          this.storeReviewer(body.reviewer);
          this.reason.set('');
          this.notice.set(`${done} ${outcome(lookup)}`);
          this.handOn(lookup);
          this.refocus(body.field);
        },
        error: (err: ApiError) => {
          if (vehicleId !== this.lookup().vehicle_id) return;
          this.saving.set(false);
          // The car is resolved today and would not stay so: the same request waits for a yes.
          const ask = question(err, waiting.what);
          if (ask) this.asking.set(ask);
          else if (!this.refuse(err, vehicleId, body.field, label)) this.pending.set(null);
        },
      });
  }

  /** "Try again": the correction, or the apply, proposal or undo for several cars, sent again unchanged. */
  protected again(): void {
    if (this.pending()) this.send();
    else this.sendDecision();
  }

  /** The person saw what the car would lose: the same body and operation id, now confirmed. */
  protected saveAnyway(): void {
    const waiting = this.pending();
    if (!waiting || this.saving()) return;
    this.pending.set({ ...waiting, body: { ...waiting.body, confirm_change: true } });
    this.send();
  }

  /** "Cancel" on that question: nothing is saved, and focus goes back to where it was asked from. */
  protected keep(): void {
    const field = this.pending()?.body.field;
    this.reset();
    if (!field) return;
    afterNextRender(
      () => {
        const save = this.editing() === field ? this.primary()?.nativeElement : undefined;
        if (save && !save.disabled) save.focus();
        else this.refocus(field);
      },
      { injector: this.injector },
    );
  }

  /**
   * Say why a request was not carried out. True when the same request is worth sending
   * again unchanged ("Try again"); the caller gives it up otherwise.
   */
  private refuse(err: ApiError, vehicleId: string, field: string, label: string): boolean {
    const { code, message } = errorDetail(err);
    const release = field === STOP_FIELD;
    const stale = code ? OUT_OF_DATE[code]?.[release ? 1 : 0] : undefined;
    if (stale) {
      this.reload(vehicleId, stale);
      return false;
    }
    const busy = code ? BUSY[code] : undefined;
    if (busy) {
      this.error.set({ message: busy, retry: true });
      return true;
    }
    const refusal = code ? REFUSALS[code] : undefined;
    if (refusal) {
      // What a field accepts (a range, a closed list) is the server's to say.
      const why =
        code === 'invalid_value' && message
          ? `Not saved. ${message}`
          : code === 'reason_required' && release
            ? RELEASE_UNREASONED
            : refusal;
      this.error.set({ message: why.replace('{label}', label), retry: false });
      return false;
    }
    if (transient(err)) {
      this.error.set({ message: 'Not saved. Try again.', retry: true });
      return true;
    }
    this.error.set({ message: `Not saved. ${message ?? NOT_ACCEPTED}`, retry: false });
    return false;
  }

  // --- One correction for several cars: the check, and what is done with its result -------

  /** Start the check of the picked scope and follow it until it ends. Saves nothing. */
  private startCheck(
    row: Row,
    action: 'set' | 'ignore',
    value: string | null,
    pick: CorrectionScopeChoice,
  ): void {
    const result = this.lookup();
    const vehicleId = result.vehicle_id;
    const fingerprint = result.evidence_fingerprint;
    if (!vehicleId || !fingerprint) return;
    const { option, narrow } = pick;
    this.reset();
    this.endCheck();
    this.acted.set(row.field);
    this.check.set('starting');
    this.checking = this.api
      .startCorrectionPreview(vehicleId, {
        field: row.field,
        action,
        value,
        scope: {
          kind: option.kind,
          rung: option.rung ?? null,
          conditions: option.kind === 'like_this' ? (option.conditions ?? null) : null,
          narrow,
        },
        evidence_fingerprint: fingerprint,
      })
      .pipe(
        switchMap((job) => this.follow(job)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (job) => this.check.set(job),
        error: (err: ApiError) => {
          const started = this.check() !== 'starting';
          this.endCheck();
          this.unchecked(err, vehicleId, row.label, started);
          // The progress line is gone: focus goes back to the button that asked for the check.
          this.focusPrimary();
        },
      });
  }

  /** The job as it is, then as the server reports it about once a second until it has ended. */
  private follow(job: CorrectionPreviewJob): Observable<CorrectionPreviewJob> {
    if (job.status !== 'running') return of(job);
    const wait = this.timing.pollMs;
    const polls = timer(wait).pipe(
      switchMap(() => this.api.correctionPreview(job.preview_id)),
      // A poll lost on its way is asked again; a refusal ends the check.
      retry({
        count: 3,
        delay: (err: ApiError) => (transient(err) ? timer(wait) : throwError(() => err)),
      }),
      repeat(),
      takeWhile((latest) => latest.status === 'running', true),
    );
    return concat(of(job), polls);
  }

  /** Why a check did not start, or could not be followed. Nothing was saved either way. */
  private unchecked(err: ApiError, vehicleId: string, label: string, started: boolean): void {
    const { code, message } = errorDetail(err);
    const stale = code ? OUT_OF_DATE[code]?.[0] : undefined;
    if (stale) {
      this.reload(vehicleId, stale);
      return;
    }
    const refusal = code ? REFUSALS[code] : undefined;
    const said = refusal
      ? code === 'invalid_value' && message
        ? `Not saved. ${message}`
        : refusal.replace('{label}', label)
      : started
        ? CHECK_LOST
        : transient(err)
          ? `${CHECK_NOT_STARTED} Try again.`
          : `${CHECK_NOT_STARTED} ${message ?? NOT_ACCEPTED}`;
    this.error.set({ message: said, retry: false });
  }

  /** "Stop": the server ends the check and the next poll shows what it found so far. */
  protected stopCheck(): void {
    const job = this.check();
    if (!job || job === 'starting' || job.status !== 'running') return;
    this.api
      .stopCorrectionPreview(job.preview_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ error: () => undefined });
  }

  /** The checked cars of one outcome, for "See these cars". */
  protected listCars(outcome: CorrectionOutcome): void {
    const job = this.check();
    if (!job || job === 'starting') return;
    const previewId = job.preview_id;
    const put = (list: CorrectionCarList) => {
      const now = this.check();
      if (now && now !== 'starting' && now.preview_id === previewId) {
        this.lists.update((lists) => ({ ...lists, [outcome]: list }));
      }
    };
    put('loading');
    this.api
      .correctionPreviewCars(previewId, outcome, CARS_LISTED)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: (page) => put(page.cars), error: () => put('failed') });
  }

  /** Leave the result ("Narrow it…", "Cancel"): back to whom the correction applies to. */
  protected dropCheck(focus = false): void {
    if (this.saving()) return;
    this.reset();
    this.endCheck();
    if (focus) this.focusPrimary();
  }

  /** Apply the checked correction to its cars, or keep it as a proposal. A new operation id. */
  protected decide(row: Row, event: 'apply' | 'propose', include: boolean): void {
    const job = this.check();
    const vehicleId = this.lookup().vehicle_id;
    const reviewer = this.reviewer().trim();
    if (!job || job === 'starting' || !vehicleId || !reviewer || this.saving()) return;
    const request = this.api.decideCorrection({
      operation_id: crypto.randomUUID(),
      preview_id: job.preview_id,
      event,
      include_changed: event === 'apply' && include,
      reviewer,
      reason: this.reason().trim() || null,
    });
    this.reset();
    this.acted.set(row.field);
    this.decision.set({
      vehicleId,
      field: row.field,
      label: row.label,
      reviewer,
      saying: event === 'apply' ? 'Applying this and matching the car again…' : 'Saving the proposal…',
      request: request.pipe(map((answer) => (event === 'apply' ? appliedInWords(answer) : PROPOSED))),
      writes: event === 'apply',
    });
    this.sendDecision();
  }

  /** "Undo for all … cars…" asks for a reason and a "Confirm" first. */
  protected askUndoAll(row: Row): void {
    const open = this.undoing() === row.field;
    this.reset();
    this.undoing.set(open ? null : row.field);
  }

  protected cancelUndo(item: HTMLElement): void {
    this.undoing.set(null);
    item.querySelector<HTMLButtonElement>('button[data-all]')?.focus();
  }

  /** Undo a decision on every car it still stands on. A new operation id. */
  protected undoAll(row: Row, group: CorrectionDecisionRef): void {
    const vehicleId = this.lookup().vehicle_id;
    const reviewer = this.reviewer().trim();
    const reason = this.reason().trim();
    if (!vehicleId || !reviewer || !reason || this.saving()) return;
    const request = this.api.withdrawCorrectionDecision(group.decision_id, {
      operation_id: crypto.randomUUID(),
      reviewer,
      reason,
    });
    this.reset();
    this.acted.set(row.field);
    this.decision.set({
      vehicleId,
      field: row.field,
      label: row.label,
      reviewer,
      saying: `Undoing this for all ${carCount(group.member_count)}…`,
      request: request.pipe(map(undoneInWords)),
      writes: true,
    });
    this.sendDecision();
  }

  /** Also "Try again": the same body with the same operation id. */
  private sendDecision(): void {
    const waiting = this.decision();
    if (!waiting || this.saving()) return;
    const { vehicleId, field, label, reviewer, request, writes } = waiting;
    this.saving.set(true);
    this.saying.set(waiting.saying);
    this.error.set(null);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (done) => {
        if (vehicleId !== this.lookup().vehicle_id) return;
        this.saving.set(false);
        this.decision.set(null);
        this.editing.set(null);
        this.undoing.set(null);
        this.shut();
        this.storeReviewer(reviewer);
        this.reason.set('');
        this.notice.set(done);
        if (writes) this.reread(vehicleId, done);
        this.refocus(field);
      },
      error: (err: ApiError) => {
        if (vehicleId !== this.lookup().vehicle_id) return;
        this.saving.set(false);
        const { code } = errorDetail(err);
        if (code && CHECK_GONE.includes(code)) this.endCheck();
        if (!this.refuse(err, vehicleId, field, label)) this.decision.set(null);
      },
    });
  }

  /** Cars were written, this one among them: read it as it is now and hand it on. */
  private reread(vehicleId: string, done: string): void {
    this.api
      .matchLookup(vehicleId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (lookup) => {
          if (vehicleId === this.lookup().vehicle_id) this.handOn(lookup);
        },
        error: () => {
          if (this.notice() === done) this.notice.set(`${done} ${NOT_READ}`);
        },
      });
  }

  /** The check is over for this screen; one still running is stopped, to free the server. */
  private endCheck(): void {
    const job = this.check();
    this.checking?.unsubscribe();
    this.checking = null;
    if (job && job !== 'starting' && job.status === 'running') {
      // Not tied to this panel's life: it may be on its way out, and the request ends by itself.
      this.api.stopCorrectionPreview(job.preview_id).subscribe({ error: () => undefined });
    }
    this.check.set(null);
    this.lists.set({});
  }

  /** The editor's step for several cars is over: nothing is asked, picked or checked. */
  private shut(): void {
    this.endCheck();
    this.scopesAsked.next(null);
    this.marking.set(false);
    this.scopes.set(null);
    this.choice.set(null);
  }

  /** Another car is shown: nothing of the one before stays open or on its way. */
  private leave(): void {
    this.shut();
    this.reset();
    this.editing.set(null);
    this.undoing.set(null);
    this.confirming.set(false);
    this.saving.set(false);
  }

  private focusPrimary(): void {
    afterNextRender(() => this.primary()?.nativeElement.focus(), { injector: this.injector });
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
    this.decision.set(null);
    this.asking.set(null);
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
