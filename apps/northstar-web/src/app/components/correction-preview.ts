import {
  ChangeDetectionStrategy,
  Component,
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
} from '@angular/core';

import type {
  CarMatch,
  CorrectionDecisionResult,
  CorrectionOutcome,
  CorrectionPreviewCar,
  CorrectionPreviewJob,
  CorrectionScopeChoice,
  CorrectionScopeCondition,
  CorrectionScopeOption,
  CorrectionWideScope,
  CorrectionWithdrawal,
} from '../core/models';

/** The checked cars of one outcome, as far as they were asked for. */
export type CorrectionCarList = CorrectionPreviewCar[] | 'loading' | 'failed';
export type CorrectionCarLists = Partial<Record<CorrectionOutcome, CorrectionCarList>>;

const NUMBER = new Intl.NumberFormat('en-US');

function num(count: number): string {
  return NUMBER.format(count);
}

/** "1 car", "4,275 cars". */
export function carCount(count: number): string {
  return `${num(count)} ${count === 1 ? 'car' : 'cars'}`;
}

/** A count with what it does, in the singular for one car. */
function some(count: number, one: string, several: string): string {
  return `${num(count)} ${count === 1 ? one : several}`;
}

/** "a", "a and b", "a, b and c". */
function listed(parts: string[]): string {
  return parts.length < 2 ? parts.join('') : `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}`;
}

/**
 * Where the matcher leaves a car it does not resolve, in plain words: what one such car
 * does, what several do, and what it is called in a list. Any other terminal reads with spaces.
 */
const ENDS: Record<string, readonly [one: string, several: string, name: string]> = {
  review_required: ['still ties', 'still tie', 'tie'],
  hard_conflict: ['conflicts', 'conflict', 'conflict'],
  provisional: ['is not certain enough', 'are not certain enough', 'not certain enough'],
  unmatched: ['finds no KType', 'find no KType', 'no KType found'],
  normalization_review: ['is stopped before matching', 'are stopped before matching', 'stopped before matching'],
  policy_excluded: ['is left out of matching', 'are left out of matching', 'left out of matching'],
  failed: ['could not be matched', 'could not be matched', 'could not be matched'],
};

function endName(match: CarMatch | null): string | null {
  if (!match) return null;
  if (match.terminal === 'resolved') return match.ktype ? `KType ${match.ktype}` : 'resolved';
  return ENDS[match.terminal]?.[2] ?? match.terminal.replace(/_/g, ' ');
}

/** The spec's `production_year` from/to: narrowed to a span rather than to one value. */
const SPANS: readonly string[] = ['production_year'];

/** What is ticked and typed for one condition of "Narrow it…". */
interface Narrowing {
  on: boolean;
  from: string;
  to: string;
}

/** The ticked conditions as the check takes them; an empty box means "has no value there". */
function narrowed(
  items: readonly (Narrowing & { field: string; span: boolean })[],
): CorrectionScopeCondition[] {
  const conditions: CorrectionScopeCondition[] = [];
  for (const item of items.filter((entry) => entry.on)) {
    const from = item.from.trim();
    const to = item.span ? item.to.trim() : '';
    if (!from && !to) conditions.push({ field: item.field, operator: 'is_empty', values: [] });
    else if (!item.span) conditions.push({ field: item.field, operator: 'equals', values: [from] });
    else {
      if (from) conditions.push({ field: item.field, operator: 'gte', values: [from] });
      if (to) conditions.push({ field: item.field, operator: 'lte', values: [to] });
    }
  }
  return conditions;
}

/** A scope as its radio button says it: the cars in plain words, with how many there are. */
function scopeText(option: CorrectionScopeOption): string {
  const count = typeof option.count === 'number' ? option.count : null;
  const uncounted = option.too_broad || option.count === null ? ' (too many to count)' : '';
  if (option.kind === 'same_data') {
    return `The${count === null ? '' : ` ${num(count)}`} cars with exactly the same data as this one${uncounted}`;
  }
  const label = option.label ?? 'All cars like this one';
  if (count === null) return `${label}${uncounted}`;
  return label.startsWith('All ') ? `All ${num(count)} ${label.slice(4)}` : `${label} (${carCount(count)})`;
}

/** One sentence of the result; `outcome` names the checked cars "See these cars" lists. */
interface Line {
  key: string;
  text: string;
  /** A second sentence about the same cars. */
  note: string | null;
  outcome: CorrectionOutcome | null;
  count: number;
}

/** What the check found: one sentence per count that is not zero, and always the lost matches. */
function sentences(job: CorrectionPreviewJob, label: string): Line[] {
  const counts = job.counts;
  const field = label.charAt(0).toLowerCase() + label.slice(1);
  const lines: Line[] = [];
  const say = (outcome: CorrectionOutcome, text: string, note: string | null = null, always = false) => {
    const count = counts[outcome];
    if (count || always) lines.push({ key: outcome, text, note, outcome: count ? outcome : null, count });
  };

  // The engine code is the one check of a new match that does not rest on the matcher itself.
  const engine = job.engine_check;
  const compared = [
    engine.agree ? some(engine.agree, 'agrees', 'agree') : '',
    engine.differ ? some(engine.differ, 'differs', 'differ') : '',
    engine.unchecked ? `${num(engine.unchecked)} could not be compared` : '',
  ].filter(Boolean);
  say(
    'gained',
    some(counts.gained, 'would resolve (it is unresolved now)', 'would resolve (they are unresolved now)'),
    engine.agree || engine.differ ? `Engine code against the new KType: ${compared.join(', ')}.` : null,
  );

  const ends = Object.entries(counts.still_unresolved_by_terminal ?? {})
    .filter(([, cars]) => cars > 0)
    .map(([terminal, cars]) => {
      const does = ENDS[terminal]?.[cars === 1 ? 0 : 1] ?? terminal.replace(/_/g, ' ');
      return `${num(cars)} ${does}`;
    });
  say(
    'still_unresolved',
    some(counts.still_unresolved, 'stays unresolved', 'stay unresolved') +
      (ends.length ? ` (${ends.join(', ')})` : ''),
  );
  say(
    'same',
    some(counts.same, 'is resolved and stays on the same KType', 'are resolved and stay on the same KType'),
  );
  say('moved', movedText(counts.moved));
  // Said at zero too: it is what a person looks for before applying.
  say('lost', lostText(counts.lost), null, true);
  say('worse', worseText(counts.worse));

  if (counts.with_choice) {
    const chosen = counts.with_choice;
    const differ = counts.choice_would_disagree;
    lines.push({
      key: 'with_choice',
      text:
        chosen === 1
          ? '1 car has a KType chosen by a person. The choice stays and will be marked for another look.'
          : `${num(chosen)} cars have a KType chosen by a person. The choices stay and will be marked for another look.`,
      note: !differ
        ? null
        : chosen === 1
          ? 'The matcher would then resolve it to another KType than the chosen one.'
          : `For ${num(differ)} of them the matcher would then resolve to another KType than the chosen one.`,
      outcome: null,
      count: chosen,
    });
  }

  const alone = (count: number) => (count === 1 ? 'It is left as it is.' : 'They are left as they are.');
  say(
    'already_corrected',
    `${some(counts.already_corrected, 'car already has', 'cars already have')} a person's correction for ${field}. ${alone(counts.already_corrected)}`,
  );
  const unchanged = counts.no_effect === 1 ? 'Nothing changes for it.' : 'Nothing changes for them.';
  say(
    'no_effect',
    job.action === 'set'
      ? `${some(counts.no_effect, 'car already has', 'cars already have')} this value. ${unchanged}`
      : `${some(counts.no_effect, 'car has', 'cars have')} no value there to mark as wrong. ${unchanged}`,
  );
  say(
    'not_like_this',
    `${some(counts.not_like_this, 'car', 'cars')} turned out to have a different ${field} than this one. ${alone(counts.not_like_this)}`,
  );
  return lines;
}

function movedText(count: number): string {
  return some(count, 'resolved car would move to another KType', 'resolved cars would move to another KType');
}

function lostText(count: number): string {
  return some(count, 'resolved car would lose its match', 'resolved cars would lose their match');
}

function worseText(count: number): string {
  return some(count, 'unresolved car would get harder to match', 'unresolved cars would get harder to match');
}

/** What an apply did, for the line that says it is done. */
export function appliedInWords(answer: CorrectionDecisionResult): string {
  // Of the cars that were written; the check's own count only when the answer does not say.
  const gained = answer.written_by_outcome?.gained ?? answer.counts?.gained ?? 0;
  const changed = answer.skipped?.changed_since_check ?? 0;
  const corrected = answer.skipped?.corrected_meanwhile ?? 0;
  const left = (count: number) => (count === 1 ? 'was left as it is' : 'were left as they are');
  return [
    `Applied to ${carCount(answer.written ?? 0)}.`,
    gained ? `${some(gained, 'now resolves', 'now resolve')}.` : '',
    changed ? `${num(changed)} changed since the check and ${left(changed)}.` : '',
    corrected
      ? `${some(corrected, 'was', 'were')} corrected by a person meanwhile and ${left(corrected)}.`
      : '',
  ]
    .filter(Boolean)
    .join(' ');
}

/** What undoing a decision did; every car it left alone was changed by a person since. */
export function undoneInWords(answer: CorrectionWithdrawal): string {
  const left = answer.left_changed ?? 0;
  const kept =
    left === 1
      ? ' 1 was changed by a person since and was left as it is.'
      : left
        ? ` ${num(left)} were changed by a person since and were left as they are.`
        : '';
  return `Undone for ${carCount(answer.withdrawn ?? 0)}.${kept}`;
}

let instances = 0;

/**
 * A correction for more than one car: whom it applies to, the check of what it would change
 * car by car, and what may be done with the result. Nothing is saved before a check, and a
 * car that would lose or change its KType is left out unless the person includes it.
 *
 * Presentational -- the container sends the requests and holds the check; this says it in
 * sentences. It sits inside the editor of the field being corrected, whose own button reads
 * "Save" for this car alone and "Check" for any other choice made here.
 */
@Component({
  selector: 'ns-correction-preview',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (job(); as current) {
      @if (current.status === 'running') {
        <div class="line">
          <progress
            aria-label="Cars checked"
            [max]="target() || 1"
            [value]="current.checked"
          ></progress>
          <span role="status">Checking {{ n(current.checked) }} of {{ cars(target()) }}…</span>
          <button type="button" aria-label="Stop the check" (click)="stop.emit()">Stop</button>
        </div>
        @if (current.affected > current.cap) {
          <p class="muted small">
            This would touch {{ cars(current.affected) }}. At most {{ n(current.cap) }} can be checked
            here.
          </p>
        }
      } @else if (result(); as found) {
        <h5 #heading tabindex="-1">{{ found.heading }}</h5>
        @if (found.lines.length) {
          <p class="muted small">With {{ change() }}: {{ current.scope.label }}.</p>
          <ul>
            @for (line of found.lines; track line.key) {
              <li>
                {{ line.text }}
                @if (line.note) {
                  <span class="muted">{{ line.note }}</span>
                }
                @if (line.outcome; as outcome) {
                  <details (toggle)="onList($event, outcome)">
                    <summary [attr.aria-label]="'See these cars: ' + line.text">See these cars</summary>
                    @if (lists()[outcome]; as list) {
                      @if (list === 'loading') {
                        <p class="muted">Loading…</p>
                      } @else if (list === 'failed') {
                        <p class="error" role="alert">
                          These cars could not be listed.
                          <button
                            type="button"
                            [attr.aria-label]="'Try again to list these cars: ' + line.text"
                            (click)="seeCars.emit(outcome)"
                          >
                            Try again
                          </button>
                        </p>
                      } @else {
                        <ul class="cars">
                          @for (car of list; track car.vehicle_id) {
                            <li><span class="mono">{{ car.plate ?? car.vehicle_id }}</span> {{ ends(car) }}</li>
                          }
                        </ul>
                        @if (list.length < line.count) {
                          <p class="muted small">
                            The first {{ n(list.length) }} of {{ n(line.count) }} are listed.
                          </p>
                        }
                      }
                    }
                  </details>
                }
              </li>
            }
          </ul>
        }
        @if (found.harm; as harm) {
          <div class="note">
            <p>{{ harm.text }}</p>
            <label class="plain">
              <input
                type="checkbox"
                [checked]="include()"
                [disabled]="saving()"
                (change)="onInclude($event, current)"
              />
              {{ harm.include }}
            </label>
          </div>
        }
        @if (found.blocked; as why) {
          <p class="note" role="status" [id]="uid + '-why'">{{ why }}</p>
        }
        @if (found.applies) {
          <label>
            Reason
            <textarea
              rows="1"
              maxlength="1000"
              [disabled]="saving()"
              [attr.aria-describedby]="reasoned() ? null : uid + '-reason'"
              [value]="reason()"
              (input)="onReason($event)"
            ></textarea>
          </label>
          @if (!reasoned()) {
            <p class="muted small" [id]="uid + '-reason'">Give a reason to apply this to several cars.</p>
          }
        }
        <div class="line">
          @if (found.applies) {
            <button
              type="button"
              [disabled]="saving() || !reasoned() || !applyCount()"
              [attr.aria-busy]="saving()"
              [attr.aria-describedby]="reasoned() ? null : uid + '-reason'"
              (click)="apply.emit(include())"
            >
              Apply to {{ cars(applyCount()) }}
            </button>
          }
          @if (found.narrows) {
            <button
              type="button"
              [disabled]="saving()"
              [attr.aria-describedby]="uid + '-why'"
              (click)="narrowAgain()"
            >
              Narrow it…
            </button>
          }
          @if (found.proposes) {
            <button
              type="button"
              [disabled]="saving()"
              [attr.aria-busy]="saving()"
              [attr.aria-describedby]="uid + '-why'"
              (click)="propose.emit()"
            >
              Save as a proposal
            </button>
          }
          <button
            type="button"
            aria-label="Cancel: back to whom this applies to"
            [disabled]="saving()"
            (click)="cancel.emit()"
          >
            Cancel
          </button>
        </div>
      }
    } @else if (check() === 'starting') {
      <p class="muted" role="status">Counting the cars…</p>
    } @else {
      <fieldset [disabled]="saving()">
        <legend>Apply to</legend>
        @for (item of options(); track $index) {
          <label class="plain">
            <input
              type="radio"
              [name]="uid"
              [checked]="item.option === picked()"
              (change)="pick(item.option)"
            />
            {{ item.text }}
          </label>
        }
      </fieldset>
      @if (scopes() === null) {
        <p class="muted small">Looking for other cars like this one…</p>
      } @else if (scopes() === 'failed') {
        <p class="muted small">
          Could not look for other cars like this one.
          <button
            type="button"
            aria-label="Try again to look for other cars like this one"
            [disabled]="saving()"
            (click)="lookAgain.emit()"
          >
            Try again
          </button>
        </p>
      } @else if (options().length === 1) {
        <p class="muted small">No other car is like this one.</p>
      }
      @if (picked(); as option) {
        @if (uncounted()) {
          <p class="muted small">Too many cars to count. Narrow the group before checking it.</p>
        }
        @if (narrows().length) {
          <button
            type="button"
            [disabled]="saving()"
            [attr.aria-expanded]="narrowOpen()"
            (click)="toggleNarrow()"
          >
            Narrow it…
          </button>
          @if (narrowOpen()) {
            <fieldset [disabled]="saving()">
              <legend>Only the cars with</legend>
              @for (item of narrows(); track item.field) {
                <div class="line">
                  <label class="plain">
                    <input
                      #narrowBox
                      type="checkbox"
                      [checked]="item.on"
                      (change)="onNarrow(item.field, $event)"
                    />
                    {{ item.label }}
                  </label>
                  @if (item.span) {
                    <input
                      type="text"
                      inputmode="numeric"
                      maxlength="4"
                      size="6"
                      [attr.aria-label]="item.label + ' from'"
                      [disabled]="!item.on"
                      [value]="item.from"
                      (input)="onNarrowText(item.field, 'from', $event)"
                    />
                    <span>to</span>
                    <input
                      type="text"
                      inputmode="numeric"
                      maxlength="4"
                      size="6"
                      [attr.aria-label]="item.label + ' to'"
                      [disabled]="!item.on"
                      [value]="item.to"
                      (input)="onNarrowText(item.field, 'to', $event)"
                    />
                  } @else {
                    <input
                      type="text"
                      maxlength="80"
                      placeholder="none"
                      [attr.aria-label]="item.label + ' is'"
                      [disabled]="!item.on"
                      [value]="item.from"
                      (input)="onNarrowText(item.field, 'from', $event)"
                    />
                  }
                </div>
              }
              <p class="muted small">
                Each is filled in from this car. An empty box means cars with no value there.
              </p>
            </fieldset>
          }
        }
      }
    }
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      align-self: stretch;
      gap: 0.35rem;
    }
    h5, p, ul { margin: 0; }
    h5 { font-size: inherit; }
    ul { padding-left: 1.1rem; }
    ul.cars { padding: 0; list-style: none; }
    fieldset {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      min-width: 0;
      margin: 0;
      padding: 0;
      border: 0;
    }
    .line { display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem; }
    label { display: flex; flex-direction: column; align-self: stretch; gap: 0.15rem; }
    label, legend { padding: 0; font-weight: 600; }
    label.plain { flex-direction: row; align-items: baseline; align-self: auto; gap: 0.3rem; font-weight: 400; }
    progress { width: 12rem; max-width: 100%; }
    .note {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      padding: 0.3rem 0.5rem;
      border-left: 3px solid #c0392b;
      background: #fdf1f0;
    }
    input, textarea { font: inherit; font-weight: 400; padding: 0.2rem 0.35rem; }
    textarea { resize: vertical; }
    button { font: inherit; cursor: pointer; }
    button:disabled { cursor: not-allowed; }
    summary { cursor: pointer; }
    .small { font-size: 0.72rem; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { color: #8a2020; }
  `,
})
export class CorrectionPreview {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  /** The corrected field's label, as the car's lookup gives it. */
  readonly label = input.required<string>();
  /** What the correction puts in force, to follow "With": "Drive type fwd". */
  readonly change = input.required<string>();
  /** What the scopes call offers; null while it is asked, `failed` when it could not be. */
  readonly scopes = input<CorrectionScopeOption[] | 'failed' | null>(null);
  /** The scope picked beyond this car; null is "Only this car". */
  readonly choice = input<CorrectionScopeChoice | null>(null);
  /** The check: asked for (`starting`), then the job as the server last reported it. */
  readonly check = input<CorrectionPreviewJob | 'starting' | null>(null);
  /** The lists behind the counts, by outcome, as far as they were asked for. */
  readonly lists = input<CorrectionCarLists>({});
  /** An apply or a proposal is on its way. */
  readonly saving = input(false);
  /** The reason the container's other actions use too; an apply needs one. */
  readonly reason = model('');

  /** Another scope was picked or narrowed; null for "Only this car". */
  readonly choose = output<CorrectionScopeChoice | null>();
  /** "Try again" after the scopes could not be looked up. */
  readonly lookAgain = output<void>();
  readonly stop = output<void>();
  /** A list was opened: the checked cars of this outcome are wanted. */
  readonly seeCars = output<CorrectionOutcome>();
  /** Apply; true when the cars that would be harmed are included. */
  readonly apply = output<boolean>();
  readonly propose = output<void>();
  /** Leave the result to pick a narrower group. */
  readonly narrow = output<void>();
  /** Leave the result; the container puts focus back on its "Check" button. */
  readonly cancel = output<void>();

  protected readonly uid = `correction-preview-${instances++}`;

  protected readonly narrowOpen = signal(false);
  /** What was ticked or typed under "Narrow it…", by field; the rest is as this car has it. */
  private readonly typed = signal<Record<string, Narrowing>>({});
  /** The check whose harmed cars the person ticked to include. */
  private readonly included = signal<string | null>(null);

  private readonly heading = viewChild<ElementRef<HTMLElement>>('heading');
  private readonly narrowBox = viewChild<ElementRef<HTMLInputElement>>('narrowBox');

  constructor() {
    // The result replaces the progress line: move focus to its heading so it is read from there.
    effect(() => this.heading()?.nativeElement.focus());
  }

  protected readonly job = computed(() => {
    const current = this.check();
    return current === 'starting' ? null : current;
  });
  /** How many cars the check will look at: the scope's, up to what one check may hold. */
  protected readonly target = computed(() => {
    const current = this.job();
    return current ? Math.min(current.affected, current.cap) : 0;
  });
  protected readonly reasoned = computed(() => this.reason().trim().length > 0);
  protected readonly picked = computed(() => this.choice()?.option ?? null);

  /** "Only this car", then every scope that holds another car (or too many to count). */
  protected readonly options = computed(() => {
    const offered = this.scopes();
    const others = (Array.isArray(offered) ? offered : []).filter(
      (option): option is CorrectionWideScope =>
        option.kind !== 'this_car' && (typeof option.count !== 'number' || option.count > 1),
    );
    return [
      { option: null as CorrectionWideScope | null, text: 'Only this car' },
      ...others.map((option) => ({ option, text: scopeText(option) })),
    ];
  });

  /** The picked scope cannot be counted, and nothing narrows it yet. */
  protected readonly uncounted = computed(() => {
    const pick = this.choice();
    return !!pick && (pick.option.too_broad || pick.option.count === null) && !pick.narrow.length;
  });

  /** The conditions the picked scope can be narrowed by, each as ticked and typed. */
  protected readonly narrows = computed(() =>
    (this.picked()?.narrowable ?? []).map((entry) => {
      const own = entry.value ?? '';
      const typed = this.typed()[entry.field] ?? { on: false, from: own, to: own };
      return { field: entry.field, label: entry.label, span: SPANS.includes(entry.field), ...typed };
    }),
  );

  protected readonly include = computed(() => {
    const current = this.job();
    return !!current && this.included() === current.preview_id;
  });

  /** How many cars an apply would write, with the harmed ones when they are included. */
  protected readonly applyCount = computed(() => {
    const current = this.job();
    if (!current) return 0;
    const { lost, moved, worse } = current.counts;
    return current.would_write + (this.include() ? lost + moved + worse : 0);
  });

  /** The ended check in sentences, and what may be done with it. */
  protected readonly result = computed(() => {
    const current = this.job();
    if (!current || current.status === 'running') return null;
    const counts = current.counts;
    const harmed = counts.lost + counts.moved + counts.worse;
    const failed = current.status === 'failed';
    const complete = current.complete && current.status === 'done';
    const narrowable = !!this.picked()?.narrowable?.length;
    const way = narrowable ? 'Narrow the group, or save' : 'Save';
    const reached = `${num(current.checked)} of ${carCount(this.target())}`;

    let heading = `What would change for the ${carCount(current.affected)}`;
    let blocked: string | null = null;
    // A check that is not complete, or one that harms as much as it fixes, is never applied
    // from here: the group is narrowed, or the correction kept as a proposal.
    let narrows = false;
    let proposes = false;
    if (failed) {
      heading = 'The check could not be finished';
      blocked = 'Nothing was changed. Check again.';
    } else if (!complete && !current.checked) {
      heading = 'No car was checked';
      narrows = narrowable;
      blocked =
        current.status === 'cancelled'
          ? 'The check was stopped before it reached a car.'
          : 'The check ended before it reached a car. Check again.';
    } else if (!complete) {
      heading = `What would change for the ${carCount(current.checked)} that ${current.checked === 1 ? 'was' : 'were'} checked`;
      narrows = narrowable;
      proposes = true;
      const why =
        current.status === 'cancelled'
          ? `The check was stopped after ${reached}`
          : current.affected > current.cap
            ? `This would touch ${carCount(current.affected)}. At most ${num(current.cap)} can be checked here`
            : `The check ran out of time after ${reached}`;
      blocked = `${why}, so it cannot be applied from here. ${way} it as a proposal to be measured on all cars first.`;
    } else if (current.blocked_by.includes('harms_more_than_it_fixes')) {
      narrows = narrowable;
      proposes = true;
      blocked = `This would fix ${carCount(counts.gained)} and harm ${num(harmed)}. It cannot be applied from here. ${way} it as a proposal.`;
    } else if (current.blocked_by.includes('nothing_to_apply') || (!current.would_write && !harmed)) {
      blocked = 'No car in this group would take the correction, so there is nothing to apply.';
    } else if (current.blocked_by.includes('preview_expired')) {
      blocked = 'The check is too old. Check again.';
    } else if (!current.can_apply) {
      blocked = 'This cannot be applied from here.';
    }

    const applies = blocked === null;
    const harms = [
      counts.moved ? movedText(counts.moved) : '',
      counts.lost ? lostText(counts.lost) : '',
      counts.worse ? worseText(counts.worse) : '',
    ].filter(Boolean);
    return {
      heading,
      lines: failed || !current.checked ? [] : sentences(current, this.label()),
      harm:
        applies && harmed
          ? {
              text: `${listed(harms)}. ${harmed === 1 ? 'It is left out unless you include it.' : 'They are left out unless you include them.'}`,
              include:
                harmed === 1 ? 'Include this car; I looked at it' : `Include these ${carCount(harmed)}; I looked at them`,
            }
          : null,
      blocked,
      applies,
      narrows,
      proposes,
    };
  });

  protected n(count: number): string {
    return num(count);
  }

  protected cars(count: number): string {
    return carCount(count);
  }

  /** One checked car as its list says it: "tie → KType 000010064". */
  protected ends(car: CorrectionPreviewCar): string {
    const before = endName(car.before);
    const after = endName(car.after);
    return before && after ? `${before} → ${after}` : (before ?? after ?? '');
  }

  /** Another scope starts as this car has it: nothing narrowed yet. */
  protected pick(option: CorrectionWideScope | null): void {
    this.typed.set({});
    this.narrowOpen.set(false);
    this.choose.emit(option ? { option, narrow: [] } : null);
  }

  /** "Narrow it…" opens the conditions under it, or folds them away; what was ticked stays. */
  protected toggleNarrow(): void {
    this.narrowOpen.update((open) => !open);
    if (this.narrowOpen()) this.focusNarrowing();
  }

  protected onNarrow(field: string, event: Event): void {
    this.narrowBy(field, { on: (event.target as HTMLInputElement).checked });
  }

  protected onNarrowText(field: string, end: 'from' | 'to', event: Event): void {
    this.narrowBy(field, { [end]: (event.target as HTMLInputElement).value });
  }

  private narrowBy(field: string, change: Partial<Narrowing>): void {
    const option = this.picked();
    const now = this.narrows().find((item) => item.field === field);
    if (!option || !now) return;
    const { on, from, to } = now;
    this.typed.update((typed) => ({ ...typed, [field]: { on, from, to, ...change } }));
    this.choose.emit({ option, narrow: narrowed(this.narrows()) });
  }

  /** The harmed cars are in or out for the check on screen; the next check starts without them. */
  protected onInclude(event: Event, current: CorrectionPreviewJob): void {
    this.included.set((event.target as HTMLInputElement).checked ? current.preview_id : null);
  }

  protected onReason(event: Event): void {
    this.reason.set((event.target as HTMLTextAreaElement).value);
  }

  /** A list is read when it is first opened, and again after it could not be. */
  protected onList(event: Event, outcome: CorrectionOutcome): void {
    const list = this.lists()[outcome];
    if ((event.target as HTMLDetailsElement).open && (!list || list === 'failed')) {
      this.seeCars.emit(outcome);
    }
  }

  /** From a result that cannot be applied: back to the group, with its conditions open. */
  protected narrowAgain(): void {
    this.narrowOpen.set(this.narrows().length > 0);
    this.narrow.emit();
    this.focusNarrowing();
  }

  /** Focus goes to the first condition; without any, to the scope that is picked. */
  private focusNarrowing(): void {
    afterNextRender(
      () => {
        const box = this.narrowBox()?.nativeElement;
        const radio = this.host.nativeElement.querySelector<HTMLInputElement>('input[type="radio"]:checked');
        (box ?? radio)?.focus();
      },
      { injector: this.injector },
    );
  }
}
