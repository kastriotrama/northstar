import { DecimalPipe, NgTemplateOutlet } from '@angular/common';
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
import { catchError, map, of, startWith, switchMap, tap } from 'rxjs';

import { Api } from '../core/api';
import { describeReason, fieldName } from '../core/match-reasons';
import type {
  KTypeCandidate,
  KTypeChoiceAction,
  KTypeChoiceHistory,
  KTypeChoiceRequest,
  MatchBucket,
  VehicleMatchLookup,
} from '../core/models';
import { FactCorrections } from './fact-corrections';
import { type ChoiceError, KTypeChoice, NAME_HINT_ID, type PendingChoice } from './ktype-choice';

const REVIEWER_STORAGE_KEY = 'match-review-reviewer';

/** Refusals that mean the screen is out of date: say so and show the current state. */
const REFUSALS: Record<string, string> = {
  choice_changed:
    "Someone else changed this car's choice while you were looking. The current one is shown.",
  evidence_changed:
    "This car's matching changed since you opened it. Check the candidates and choose again.",
  ktype_not_a_candidate: "That KType is no longer among this car's candidates.",
  nothing_to_withdraw: 'There is no choice to withdraw.',
};

interface ApiError {
  status?: number;
  error?: { detail?: unknown };
}

/**
 * `detail` is a string on older endpoints, `{code, message}` on the choice ones, and a
 * list of `{msg}` when the request itself was malformed (a 422 from validation).
 */
function errorDetail(err: ApiError): { code: string | null; message: string | null } {
  const detail = err?.error?.detail;
  if (typeof detail === 'string') return { code: null, message: detail };
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item: unknown) => (item as { msg?: unknown } | null)?.msg)
      .filter((msg): msg is string => typeof msg === 'string');
    return { code: null, message: messages.join('; ') || null };
  }
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const { code, message } = detail as { code?: unknown; message?: unknown };
    return {
      code: typeof code === 'string' ? code : null,
      message: typeof message === 'string' ? message : null,
    };
  }
  return { code: null, message: null };
}

interface Chip {
  field: string;
  text: string;
  state: 'conflict' | 'missing' | 'ok';
}

const SOURCE_NAMES: Record<string, string> = {
  transportstyrelsen: 'TS',
  ais: 'AIS',
  review: 'a review',
  rule: 'a learned rule',
  derived: 'its own data',
};

function sourceName(source: string): string {
  return SOURCE_NAMES[source] ?? source;
}

/** A comparable value per field, the way the backend's separating fields compare them. */
const FIELD_VALUE: Record<string, (candidate: KTypeCandidate) => string> = {
  model: (c) => c.model,
  year: (c) => `${c.year_from ?? ''}-${c.year_to ?? ''}`,
  fuels: (c) => c.fuels.join(','),
  engine_code: (c) => c.engine_codes.join(','),
  displacement_cc: (c) => String(c.displacement_cc ?? ''),
  power_kw: (c) => String(c.power_kw ?? ''),
  drive_type: (c) => c.drive_type ?? '',
  bodywork: (c) => c.bodyworks.join(','),
};

const BUCKET_LABELS: Record<MatchBucket, string> = {
  one: 'One KType',
  several: 'Several KTypes',
  none: 'No KType',
  not_matchable: 'Not matchable',
};

/**
 * Which TecDoc KTypes one car could be, read from the real matcher.
 *
 * Shown inside the Vehicles record panel for the vehicle open there, matched on its
 * merged values -- an engine code AIS supplied reaches the matcher. Every
 * candidate the matcher weighed is listed with its catalog values; a value that
 * conflicts with the car is marked, so why a KType was ruled out reads off the chip.
 */
@Component({
  selector: 'ns-ktype-candidates',
  imports: [DecimalPipe, NgTemplateOutlet, KTypeChoice, FactCorrections],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (state(); as current) {
      @if (current.loading) {
        <p class="muted">Matching against TecDoc…</p>
      } @else if (current.error) {
        <p class="error">{{ current.error }}</p>
      } @else if (current.lookup; as result) {
        @if (result.effective_source === 'person') {
          <p class="effective">
            @if (result.effective_ktype) {
              This car's KType: <b class="mono">{{ result.effective_ktype }}</b> · chosen by a person
            } @else {
              This car has no KType · a person recorded “none of these”
            }
          </p>
        }
        <div class="head">
          @if (result.effective_source === 'person') {
            <span class="muted">Matcher:</span>
          }
          <span class="bucket bucket--{{ result.bucket }}">{{ bucketLabel(result.bucket) }}</span>
          <span class="muted">
            pipeline: <b>{{ result.terminal }}</b>
            @if (result.confidence !== null) {
              · {{ result.confidence | number: '1.0-2' }}
            }
          </span>
        </div>

        @if (result.verdict) {
          <p class="verdict verdict--{{ result.terminal }}">
            <b>{{ result.terminal.replace('_', ' ') }}:</b> {{ result.verdict }}
          </p>
        }

        @if (gap(); as text) {
          <p class="gap">{{ text }}</p>
        }

        @if (explained().length) {
          <ul class="why">
            @for (item of explained(); track item.code) {
              <li [title]="item.code">{{ item.text }}</li>
            }
          </ul>
        }

        @if (result.inputs; as seen) {
          <details class="block">
            <summary>What the matcher saw</summary>
            <dl class="kv">
              <dt>manufacturer</dt>
              <dd>{{ seen.manufacturer }}</dd>
              <dt>model</dt>
              <dd>
                {{ seen.model_values.join(' / ') }}
                @if (seen.model_recovered_from) {
                  <span class="muted">({{ seen.model_recovered_from }})</span>
                }
              </dd>
              <dt>year</dt>
              <dd [class.gone]="seen.production_year === null">{{ seen.production_year ?? '—' }}</dd>
              <dt>fuel</dt>
              <dd [class.gone]="!seen.fuels.length">{{ seen.fuels.join(', ') || '—' }}</dd>
              <dt>engine code</dt>
              <dd [class.gone]="!seen.engine_code" [class.ruled]="filled('engine_code')">
                {{ seen.engine_code ?? '—' }}
              </dd>
              <dt>power</dt>
              <dd [class.gone]="seen.power_kw === null" [class.ruled]="filled('power_kw')">
                {{ seen.power_kw ?? '—' }} kW
              </dd>
              <dt>displacement</dt>
              <dd [class.gone]="seen.displacement_cc === null" [class.ruled]="filled('displacement_cc')">
                {{ seen.displacement_cc ?? '—' }} cc
              </dd>
              <dt>drive</dt>
              <dd [class.gone]="!seen.drive_type" [class.ruled]="filled('drive_type')">
                {{ seen.drive_type ?? '—' }}
              </dd>
              <dt>bodywork</dt>
              <dd [class.gone]="!seen.bodywork_form" [class.ruled]="filled('bodywork_form')">
                {{ seen.bodywork_form ?? '—' }}
              </dd>
            </dl>
            @if (overlaidNote(); as note) {
              <p class="muted note">{{ note }}</p>
            }
          </details>
        }

        <ng-template #list>
        @for (candidate of result.candidates; track candidate.ktype) {
          <div class="candidate" [class.candidate--out]="!candidate.compatible">
            <div class="candidate__head">
              <span class="mono">{{ candidate.ktype }}</span>
              <span class="candidate__model">{{ candidate.model }}</span>
              <span
                [class.muted]="!candidate.conflicting_fields.includes('year')"
                [class.chip--conflict]="candidate.conflicting_fields.includes('year')"
                >{{ years(candidate) }}</span
              >
              @if (candidate.ktype === result.top_ktype) {
                <span class="tag">top</span>
              }
              @if (isChosen(candidate)) {
                <span class="tag tag--person">chosen by a person</span>
              }
              @if (candidate.candidate_only) {
                <span class="tag tag--warn" title="Candidate-only KType: never auto-resolved">candidate-only</span>
              }
            </div>
            <div class="chips">
              @for (chip of chips(candidate); track chip.field) {
                <span class="chip chip--{{ chip.state }}" [title]="chip.field">{{ chip.text }}</span>
              }
            </div>
            @if (candidate.conflicting_fields.length) {
              <p class="candidate__why">Ruled out: {{ candidate.conflicting_fields.join(', ') }}</p>
            } @else if (standing(candidate); as text) {
              <p class="candidate__why candidate__why--fits">{{ text }}</p>
            }
            @if (canChoose() && !isChosen(candidate)) {
              <button
                type="button"
                class="pick"
                [disabled]="!reviewer().trim() || saving()"
                [attr.aria-label]="chooseName(candidate)"
                [attr.aria-describedby]="reviewer().trim() ? null : hintId"
                [attr.aria-busy]="saving() && pending()?.body?.ktype === candidate.ktype"
                (click)="choose(candidate)"
              >
                {{ chooseLabel(candidate) }}
              </button>
            }
          </div>
        } @empty {
          @if (result.bucket === 'not_matchable') {
            <p class="muted">Stopped before matching: {{ result.reason_codes.join(', ') }}</p>
          } @else {
            <p class="muted">No KType cleared the matcher's threshold.</p>
          }
        }

        @if (result.candidates.length >= result.candidate_limit) {
          <p class="muted note">
            The matcher returns at most {{ result.candidate_limit }} candidates; there may be more.
          </p>
        }

        </ng-template>

        @if (canChoose()) {
          <ns-fact-corrections
            [lookup]="result"
            [reviewer]="reviewer()"
            (reviewerChange)="onReviewer($event)"
            [(reason)]="reason"
            (corrected)="onCorrected($event)"
          />
          <ns-ktype-choice
            [lookup]="result"
            [pending]="pending()"
            [saving]="saving()"
            [error]="saveError()"
            [notice]="notice()"
            [history]="history()"
            [historyError]="historyError()"
            [reviewer]="reviewer()"
            (reviewerChange)="onReviewer($event)"
            [(reason)]="reason"
            (confirm)="confirm()"
            (cancel)="pending.set(null)"
            (none)="ask('none', null)"
            (withdraw)="ask('withdraw', null)"
            (keep)="keep()"
            (retry)="retry()"
            (historyOpened)="openHistory()"
            (historyClosed)="historyWanted.set(false)"
          >
            <ng-container [ngTemplateOutlet]="list" />
          </ns-ktype-choice>
        } @else {
          <ng-container [ngTemplateOutlet]="list" />
        }

        <details class="block">
          <summary>Reason codes</summary>
          <ul class="reasons">
            @for (reason of result.reason_codes; track reason) {
              <li class="mono">{{ reason }}</li>
            }
          </ul>
          <p class="muted note">Catalog: <span class="mono">{{ result.catalog_batch }}</span></p>
        </details>
      }
    }
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      font-size: 0.8rem;
    }
    .head {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
    }
    .effective {
      margin: 0;
      padding: 0.3rem 0.6rem;
      border-left: 3px solid #1f4d85;
      background: #eef3fb;
    }
    .bucket {
      font-weight: 650;
      padding: 0.1rem 0.5rem;
      border-radius: 999px;
    }
    .bucket--one { background: #dff3e4; color: #1c5a2e; }
    .bucket--several { background: #fff3d6; color: #7a5300; }
    .bucket--none { background: #fde4e4; color: #8a2020; }
    .bucket--not_matchable { background: var(--p-surface-100); color: var(--p-text-muted-color); }
    .verdict {
      margin: 0;
      padding: 0.4rem 0.6rem;
      border-left: 3px solid var(--p-surface-400);
      background: var(--p-surface-50);
    }
    .verdict--resolved { border-left-color: #2e8b57; }
    .verdict--provisional { border-left-color: #d99a00; }
    .verdict--review_required, .verdict--hard_conflict { border-left-color: #c0392b; }
    .why { margin: 0; padding-left: 1.1rem; }
    .why li { margin: 0.1rem 0; }
    .candidate__why--fits { color: #7a5300; }
    .gap {
      margin: 0;
      padding: 0.4rem 0.6rem;
      border-left: 3px solid #d99a00;
      background: #fffaf0;
    }
    .block summary {
      cursor: pointer;
      font-weight: 600;
    }
    .kv {
      display: grid;
      grid-template-columns: minmax(90px, 35%) 1fr;
      gap: 0.15rem 0.6rem;
      margin: 0.4rem 0 0;
    }
    .kv dt { color: var(--p-text-muted-color); }
    .kv dd { margin: 0; }
    .gone { color: var(--p-text-muted-color); opacity: 0.6; }
    .ruled { text-decoration: underline dotted; text-underline-offset: 3px; }
    .candidate {
      border: 1px solid var(--p-surface-200);
      border-radius: 6px;
      padding: 0.4rem 0.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .candidate--out { opacity: 0.7; background: var(--p-surface-50); }
    .candidate__head {
      display: flex;
      align-items: baseline;
      gap: 0.4rem;
      flex-wrap: wrap;
    }
    .candidate__model { font-weight: 600; }
    .candidate__why { margin: 0; color: #8a2020; }
    .chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
    .chip {
      padding: 0.05rem 0.4rem;
      border-radius: 4px;
      background: var(--p-surface-100);
    }
    .chip--conflict { background: #fde4e4; color: #8a2020; font-weight: 600; }
    .chip--missing { background: transparent; border: 1px dashed var(--p-surface-300); }
    .tag {
      font-size: 0.66rem;
      padding: 0.05rem 0.35rem;
      border-radius: 999px;
      background: #dde8f7;
      color: #1f4d85;
    }
    .tag--warn { background: #fff3d6; color: #7a5300; }
    .tag--person { background: #1f4d85; color: #fff; }
    .pick { align-self: flex-start; font: inherit; cursor: pointer; }
    .reasons { margin: 0.3rem 0 0; padding-left: 1rem; }
    .note { margin: 0.2rem 0 0; font-size: 0.72rem; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { margin: 0; color: #8a2020; }
  `,
})
export class KTypeCandidates {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);

  /** The NorthStar vehicle open in the panel, matched on its merged values. */
  readonly vehicleId = input.required<string>();

  /** A person's choice was recorded, changed or withdrawn; carries the refreshed lookup. */
  readonly choiceChanged = output<VehicleMatchLookup>();

  protected readonly hintId = NAME_HINT_ID;
  protected readonly reviewer = signal(this.storedReviewer());
  protected readonly reason = signal('');
  protected readonly pending = signal<PendingChoice | null>(null);
  protected readonly saving = signal(false);
  protected readonly saveError = signal<ChoiceError | null>(null);
  protected readonly notice = signal<string | null>(null);
  protected readonly history = signal<KTypeChoiceHistory | null>(null);
  protected readonly historyError = signal(false);
  /** The history is open on screen: reload it whenever the choice may have changed. */
  protected readonly historyWanted = signal(false);

  protected readonly state = signal<{
    loading: boolean;
    error: string | null;
    lookup: VehicleMatchLookup | null;
  } | null>(null);

  constructor() {
    toObservable(this.vehicleId)
      .pipe(
        tap(() => this.clearAction()),
        switchMap((id) =>
          this.api.matchLookup(id).pipe(
            map((lookup) => ({ loading: false, error: null as string | null, lookup })),
            catchError((err: ApiError) =>
              of({
                loading: false,
                error: errorDetail(err).message ?? 'Matching is unavailable right now.',
                lookup: null,
              }),
            ),
            // The first lookup builds the matcher (seconds); say so rather than sit blank.
            startWith({ loading: true, error: null, lookup: null }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((next) => this.state.set(next));
  }

  /** Choices are per vehicle, and need the evidence fingerprint the server stores them against. */
  protected readonly canChoose = computed(() => {
    const result = this.state()?.lookup;
    return !!result?.vehicle_id && !!result.evidence_fingerprint;
  });

  /** A choice or a "none of these" that stands today; a withdrawn one does not. */
  private readonly standingChoice = computed(() => {
    const choice = this.state()?.lookup?.choice;
    return choice && choice.status !== 'withdrawn' ? choice : null;
  });

  protected isChosen(candidate: KTypeCandidate): boolean {
    const choice = this.standingChoice();
    return choice?.status === 'chosen' && choice.ktype === candidate.ktype;
  }

  protected chooseLabel(candidate: KTypeCandidate): string {
    if (!candidate.compatible) return 'Choose anyway…';
    return this.standingChoice() ? 'Choose this instead' : 'Choose this KType';
  }

  /** The button's accessible name: the visible words plus which KType, so each is distinct. */
  protected chooseName(candidate: KTypeCandidate): string {
    return `${this.chooseLabel(candidate).replace('…', '')} — KType ${candidate.ktype}`;
  }

  /** Remember the name as it is typed, so it survives opening another car before a save. */
  protected onReviewer(name: string): void {
    this.reviewer.set(name);
    if (name.trim()) this.storeReviewer(name.trim());
  }

  /** One click when nothing is overridden; anything else is asked about first. */
  protected choose(candidate: KTypeCandidate): void {
    const result = this.state()?.lookup;
    if (!result) return;
    const overridesMatcher =
      result.terminal === 'resolved' && !!result.top_ktype && result.top_ktype !== candidate.ktype;
    const plain = candidate.compatible && !this.standingChoice() && !overridesMatcher;
    this.ask('choose', candidate.ktype, !plain);
  }

  /** "Keep this choice": the same choice again, on today's evidence, which clears the flags. */
  protected keep(): void {
    const choice = this.standingChoice();
    if (!choice) return;
    if (choice.status === 'none') this.ask('none', null, false);
    else this.ask('choose', choice.ktype, false);
  }

  /** A new action, hence a new operation id. */
  protected ask(action: KTypeChoiceAction, ktype: string | null, needsConfirm = true): void {
    const result = this.state()?.lookup;
    const reviewer = this.reviewer().trim();
    if (!result || !reviewer || this.saving()) return;
    const operationId = crypto.randomUUID();
    const body: KTypeChoiceRequest = {
      operation_id: operationId,
      action,
      ktype: action === 'choose' ? ktype : null,
      reviewer,
      reason: this.reason().trim() || null,
      supersedes_choice_id: result.choice?.choice_id ?? null,
      evidence_fingerprint: result.evidence_fingerprint ?? null,
    };
    this.saveError.set(null);
    this.notice.set(null);
    this.pending.set({ operationId, body, needsConfirm });
    if (!needsConfirm) this.send();
  }

  protected confirm(): void {
    this.send();
  }

  /** The same operation id and body again: the server answers a replay with what it stored. */
  protected retry(): void {
    this.send();
  }

  private send(): void {
    const waiting = this.pending();
    const vehicleId = this.vehicleId();
    if (!waiting || this.saving()) return;
    this.saving.set(true);
    this.saveError.set(null);
    this.api
      .recordKTypeChoice(vehicleId, waiting.body)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (lookup) => {
          if (vehicleId !== this.vehicleId()) return;
          this.saving.set(false);
          this.pending.set(null);
          this.reason.set('');
          this.storeReviewer(waiting.body.reviewer);
          this.state.set({ loading: false, error: null, lookup });
          this.refreshHistory();
          this.notice.set(
            waiting.body.action === 'choose'
              ? `Saved. KType ${waiting.body.ktype} chosen for this car.`
              : waiting.body.action === 'none'
                ? 'Saved. “None of these” recorded.'
                : 'Choice withdrawn.',
          );
          this.choiceChanged.emit(lookup);
        },
        error: (err: ApiError) => {
          if (vehicleId !== this.vehicleId()) return;
          this.saving.set(false);
          const { code, message } = errorDetail(err);
          const refusal = code ? REFUSALS[code] : undefined;
          const status = err?.status ?? 0;
          if (refusal) {
            this.pending.set(null);
            this.saveError.set({ message: refusal, retry: false });
            this.reload(vehicleId);
          } else if (status === 0 || status >= 500) {
            this.saveError.set({ message: 'Not saved. Try again.', retry: true });
          } else {
            this.pending.set(null);
            this.saveError.set({ message: `Not saved. ${message ?? ''}`.trim(), retry: false });
          }
        },
      });
  }

  /** Fetch the current lookup in place, so the message above the list stays on screen. */
  private reload(vehicleId: string): void {
    this.refreshHistory();
    this.api
      .matchLookup(vehicleId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (lookup) => {
          if (vehicleId === this.vehicleId()) this.state.set({ loading: false, error: null, lookup });
        },
        error: () => undefined,
      });
  }

  /** The corrections panel recorded something, or read the car again: show the lookup it hands on. */
  protected onCorrected(lookup: VehicleMatchLookup): void {
    // A choice still waiting for "Confirm" was asked about the matching as it was.
    if (!this.saving() && !this.saveError()) this.pending.set(null);
    this.state.set({ loading: false, error: null, lookup });
    this.choiceChanged.emit(lookup);
  }

  /** The history was opened (or "try again" was pressed): load it unless it is on screen. */
  protected openHistory(): void {
    this.historyWanted.set(true);
    if (!this.history()) this.loadHistory();
  }

  /** The choice may have changed: what is loaded is out of date; reload it if it is open. */
  private refreshHistory(): void {
    this.history.set(null);
    this.historyError.set(false);
    if (this.historyWanted()) this.loadHistory();
  }

  private loadHistory(): void {
    const vehicleId = this.vehicleId();
    this.historyError.set(false);
    this.api
      .ktypeChoiceHistory(vehicleId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (history) => {
          if (vehicleId === this.vehicleId()) this.history.set(history);
        },
        error: () => {
          if (vehicleId === this.vehicleId()) this.historyError.set(true);
        },
      });
  }

  private clearAction(): void {
    this.pending.set(null);
    this.saving.set(false);
    this.saveError.set(null);
    this.notice.set(null);
    this.history.set(null);
    this.historyError.set(false);
    this.historyWanted.set(false);
    this.reason.set('');
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
      localStorage.setItem(REVIEWER_STORAGE_KEY, name);
    } catch {
      // A blocked storage only costs retyping the name.
    }
  }

  /** The one sentence that says where this car's gap is. */
  protected readonly gap = computed(() => {
    const result = this.state()?.lookup;
    if (!result) return null;
    const compatible = result.candidates.filter((candidate) => candidate.compatible).length;
    if (result.bucket === 'several') {
      if (result.missing_separating_fields.length) {
        return `${compatible} KTypes fit. They differ on ${result.separating_fields.join(', ')} — and this car has no ${result.missing_separating_fields.join(' or ')}.`;
      }
      return `${compatible} KTypes fit, differing on ${result.separating_fields.join(', ') || 'nothing the matcher compares'}.`;
    }
    if (result.bucket === 'none' && result.candidates.length) {
      const fields = [...new Set(result.candidates.flatMap((c) => c.conflicting_fields))];
      return `Every candidate conflicts with this car, on ${fields.join(', ')}.`;
    }
    return null;
  });

  /** The reason codes the glossary can read, in plain words; the raw list stays below. */
  protected readonly explained = computed(() => {
    const result = this.state()?.lookup;
    if (!result) return [];
    const seen = new Set<string>();
    const items: Array<{ code: string; text: string }> = [];
    for (const code of result.reason_codes) {
      const text = describeReason(code);
      if (text && !seen.has(text)) {
        seen.add(text);
        items.push({ code, text });
      }
    }
    return items;
  });

  /**
   * How a KType that conflicts with nothing still did not win: where it differs
   * from the top candidate, and whether the car could tell them apart.
   */
  protected standing(candidate: KTypeCandidate): string | null {
    const result = this.state()?.lookup;
    if (!result || !candidate.compatible) return null;
    const top = result.candidates.find((item) => item.ktype === result.top_ktype);
    const missing = candidate.missing_fields.filter((field) => field in FIELD_VALUE);
    const unknown = missing.length
      ? ` The matcher could not compare ${missing.map(fieldName).join(' or ')}.`
      : '';
    if (!top || top.ktype === candidate.ktype) {
      return unknown ? `Fits.${unknown}` : null;
    }
    const differs = Object.keys(FIELD_VALUE).filter(
      (field) => FIELD_VALUE[field](candidate) !== FIELD_VALUE[field](top),
    );
    const lacks = differs.filter((field) => result.missing_separating_fields.includes(field));
    const on = differs.length
      ? ` It differs from the top on ${differs.map(fieldName).join(', ')}`
      : ' It matches the top on every field compared';
    const gapText = lacks.length
      ? ` — and this car has no ${lacks.map(fieldName).join(' or ')} to tell them apart.`
      : '.';
    return `Also fits, but lost to the top candidate.${on}${gapText}${unknown}`;
  }

  protected bucketLabel(bucket: MatchBucket): string {
    return BUCKET_LABELS[bucket];
  }

  /** A value the vehicle record supplied over the TS derivation: from AIS, a review, a rule. */
  protected filled(field: string): boolean {
    const lookup = this.state()?.lookup;
    if (!lookup) return false;
    return field in (lookup.overlaid_fields ?? {}) || lookup.rule_filled.includes(field);
  }

  /** Says where the underlined values came from, e.g. "engine code from AIS". */
  protected readonly overlaidNote = computed(() => {
    const lookup = this.state()?.lookup;
    const overlaid = Object.entries(lookup?.overlaid_fields ?? {});
    if (!overlaid.length) {
      return lookup?.rule_filled.length ? 'Underlined: supplied by a live rule.' : null;
    }
    const parts = overlaid.map(([field, source]) => `${field.replace(/_/g, ' ')} ${source === 'correction' ? 'corrected by a person' : `from ${sourceName(source)}`}`);
    return `Underlined: the vehicle record's value, not the TS derivation — ${parts.join(', ')}.`;
  });

  protected years(candidate: KTypeCandidate): string {
    if (candidate.year_from === null && candidate.year_to === null) return '';
    return `${candidate.year_from ?? '…'}–${candidate.year_to ?? 'now'}`;
  }

  /** Each catalog value as a chip, marked by how it compares with the car. */
  protected chips(candidate: KTypeCandidate): Chip[] {
    const state = (field: string): Chip['state'] =>
      candidate.conflicting_fields.includes(field)
        ? 'conflict'
        : candidate.missing_fields.includes(field)
          ? 'missing'
          : 'ok';
    const chips: Chip[] = [
      { field: 'year', text: this.years(candidate) || 'no years', state: state('year') },
      {
        field: 'power_kw',
        text: candidate.power_kw !== null ? `${candidate.power_kw} kW` : 'kW ?',
        state: state('power_kw'),
      },
      {
        field: 'displacement_cc',
        text: candidate.displacement_cc !== null ? `${candidate.displacement_cc} cc` : 'cc ?',
        state: state('displacement_cc'),
      },
      {
        field: 'engine_code',
        text: candidate.engine_codes.join(' / ') || 'engine ?',
        state: state('engine_code'),
      },
      { field: 'fuels', text: candidate.fuels.join(', ') || 'fuel ?', state: state('fuels') },
      { field: 'drive_type', text: candidate.drive_type ?? 'drive ?', state: state('drive_type') },
      {
        field: 'bodywork',
        text: candidate.bodyworks.join(', ') || 'body ?',
        state: state('bodywork'),
      },
    ];
    return chips.filter((chip) => chip.field !== 'year');
  }
}
