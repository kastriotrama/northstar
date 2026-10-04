/**
 * A correction for several cars, as it is said: whom it applies to, the check's progress,
 * the result in sentences, and what may be done with it. Presentational, so nothing goes
 * over the wire here; the fixtures use made-up identifiers.
 */

import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import type {
  CorrectionDecisionResult,
  CorrectionOutcome,
  CorrectionPreviewCounts,
  CorrectionPreviewJob,
  CorrectionScopeChoice,
  CorrectionScopeOption,
  CorrectionWideScope,
} from '../core/models';
import {
  type CorrectionCarLists,
  CorrectionPreview,
  appliedInWords,
  carCount,
  undoneInWords,
} from './correction-preview';

const PREVIEW_ID = 'preview-0001';

const SAME: CorrectionWideScope = {
  kind: 'same_data',
  label: 'The 4 cars with exactly the same data',
  count: 4,
};
const LIKE: CorrectionWideScope = {
  kind: 'like_this',
  label: 'All Volvo V70 cars with no drive type',
  count: 4275,
  rung: 0,
  conditions: [{ field: 'manufacturer', operator: 'equals', values: ['VOLVO'] }],
  narrowable: [
    { field: 'engine_code', label: 'Engine code', value: 'B4204T' },
    { field: 'production_year', label: 'Production year', value: '2012' },
    { field: 'fuel', label: 'Fuel', value: null },
  ],
};
const SCOPES: CorrectionScopeOption[] = [{ kind: 'this_car' }, SAME, LIKE];

function counts(overrides: Partial<CorrectionPreviewCounts> = {}): CorrectionPreviewCounts {
  return {
    gained: 0,
    lost: 0,
    moved: 0,
    same: 0,
    worse: 0,
    still_unresolved: 0,
    no_effect: 0,
    already_corrected: 0,
    not_like_this: 0,
    with_choice: 0,
    choice_would_disagree: 0,
    still_unresolved_by_terminal: {},
    ...overrides,
  };
}

/** The spec's example: 212 cars, all checked. */
function job(overrides: Partial<CorrectionPreviewJob> = {}): CorrectionPreviewJob {
  return {
    preview_id: PREVIEW_ID,
    status: 'done',
    field: 'drive_type',
    action: 'set',
    value: 'fwd',
    scope: { kind: 'like_this', label: 'All Volvo V70 cars with no drive type' },
    affected: 212,
    cap: 500,
    checked: 212,
    complete: true,
    stopped_by: null,
    counts: counts({
      gained: 171,
      moved: 2,
      same: 9,
      still_unresolved: 30,
      already_corrected: 1,
      with_choice: 1,
      still_unresolved_by_terminal: { review_required: 22, hard_conflict: 8 },
    }),
    engine_check: { agree: 150, differ: 0, unchecked: 21 },
    would_write: 210,
    can_apply: true,
    blocked_by: [],
    ...overrides,
  };
}

function squash(element: Element | null | undefined): string {
  return (element?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

interface Inputs {
  scopes?: CorrectionScopeOption[] | 'failed' | null;
  choice?: CorrectionScopeChoice | null;
  check?: CorrectionPreviewJob | 'starting' | null;
  lists?: CorrectionCarLists;
  saving?: boolean;
  reason?: string;
}

function render(inputs: Inputs = {}) {
  const fixture = TestBed.createComponent(CorrectionPreview);
  const set = (values: Inputs) => {
    for (const [name, value] of Object.entries(values)) fixture.componentRef.setInput(name, value);
  };
  fixture.componentRef.setInput('label', 'Drive type');
  fixture.componentRef.setInput('change', 'Drive type fwd');
  set({ scopes: SCOPES, ...inputs });

  const seen = {
    choose: [] as (CorrectionScopeChoice | null)[],
    seeCars: [] as CorrectionOutcome[],
    apply: [] as boolean[],
    events: [] as string[],
  };
  const panel = fixture.componentInstance;
  // The container's part: what is chosen comes back as the choice.
  panel.choose.subscribe((next) => {
    seen.choose.push(next);
    fixture.componentRef.setInput('choice', next);
  });
  panel.seeCars.subscribe((outcome) => seen.seeCars.push(outcome));
  panel.apply.subscribe((include) => seen.apply.push(include));
  panel.propose.subscribe(() => seen.events.push('propose'));
  panel.narrow.subscribe(() => seen.events.push('narrow'));
  panel.cancel.subscribe(() => seen.events.push('cancel'));
  panel.stop.subscribe(() => seen.events.push('stop'));
  panel.lookAgain.subscribe(() => seen.events.push('lookAgain'));
  fixture.detectChanges();

  const host = fixture.nativeElement as HTMLElement;
  const settle = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  };
  const button = (name: string) =>
    [...host.querySelectorAll('button')].find((item) => item.textContent?.trim() === name);
  const click = async (name: string) => {
    expect(button(name), `button "${name}"`).toBeTruthy();
    button(name)?.click();
    await settle();
  };
  const radios = () => [...host.querySelectorAll<HTMLInputElement>('input[type="radio"]')];
  const options = () => radios().map((radio) => squash(radio.closest('label')));
  const sentences = () =>
    [...host.querySelectorAll(':scope > ul > li')].map((item) => {
      const copy = item.cloneNode(true) as Element;
      copy.querySelector('details')?.remove();
      return squash(copy);
    });
  const type = async (field: HTMLInputElement | HTMLTextAreaElement, value: string) => {
    field.value = value;
    field.dispatchEvent(new Event('input'));
    await settle();
  };
  return { fixture, host, seen, set, settle, button, click, radios, options, sentences, type };
}

describe('CorrectionPreview: whom the correction applies to', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('offers “Only this car” first, then each group with its count, in a named group of radios', () => {
    const page = render();

    expect(squash(page.host.querySelector('fieldset legend'))).toBe('Apply to');
    expect(page.options()).toEqual([
      'Only this car',
      'The 4 cars with exactly the same data as this one',
      'All 4,275 Volvo V70 cars with no drive type',
    ]);
    expect(page.radios().map((radio) => radio.checked)).toEqual([true, false, false]);
    expect(new Set(page.radios().map((radio) => radio.name)).size).toBe(1);
    // Nothing beyond this car is picked: there is nothing to narrow.
    expect(page.button('Narrow it…')).toBeUndefined();
  });

  it('says so while the other cars are looked for, when there are none, and when it failed', async () => {
    const page = render({ scopes: null });
    expect(page.options()).toEqual(['Only this car']);
    expect(squash(page.host)).toContain('Looking for other cars like this one…');

    page.set({ scopes: [{ kind: 'this_car' }, { ...SAME, count: 1 }] });
    await page.settle();
    expect(page.options()).toEqual(['Only this car']);
    expect(squash(page.host)).toContain('No other car is like this one.');

    page.set({ scopes: 'failed' });
    await page.settle();
    expect(squash(page.host)).toContain('Could not look for other cars like this one.');
    await page.click('Try again');
    expect(page.seen.events).toEqual(['lookAgain']);
  });

  it('hands on the picked group, and “Only this car” again', async () => {
    const page = render();

    page.radios()[2].click();
    await page.settle();
    expect(page.seen.choose).toEqual([{ option: LIKE, narrow: [] }]);
    expect(page.radios().map((radio) => radio.checked)).toEqual([false, false, true]);

    page.radios()[0].click();
    await page.settle();
    expect(page.seen.choose.at(-1)).toBeNull();
  });

  it('narrows a wide group by conditions prefilled from this car', async () => {
    const page = render({ choice: { option: LIKE, narrow: [] } });
    const opener = page.button('Narrow it…') as HTMLButtonElement;
    expect(opener.getAttribute('aria-expanded')).toBe('false');

    await page.click('Narrow it…');
    expect(opener.getAttribute('aria-expanded')).toBe('true');
    const group = page.host.querySelectorAll('fieldset')[1];
    expect(squash(group.querySelector('legend'))).toBe('Only the cars with');
    const boxes = [...group.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    expect(boxes.map((box) => squash(box.closest('label')))).toEqual([
      'Engine code',
      'Production year',
      'Fuel',
    ]);
    const text = (name: string) =>
      group.querySelector<HTMLInputElement>(`input[aria-label="${name}"]`) as HTMLInputElement;
    expect(text('Engine code is').value).toBe('B4204T');
    expect(text('Production year from').value).toBe('2012');
    expect(text('Production year to').value).toBe('2012');
    // Focus goes to the first condition.
    expect(document.activeElement).toBe(boxes[0]);

    boxes[0].click();
    await page.settle();
    expect(page.seen.choose.at(-1)).toEqual({
      option: LIKE,
      narrow: [{ field: 'engine_code', operator: 'equals', values: ['B4204T'] }],
    });

    // A span of years; an empty box means the cars with no value there.
    boxes[1].click();
    await page.settle();
    await page.type(text('Production year from'), '2010');
    boxes[2].click();
    await page.settle();
    expect(page.seen.choose.at(-1)?.narrow).toEqual([
      { field: 'engine_code', operator: 'equals', values: ['B4204T'] },
      { field: 'production_year', operator: 'gte', values: ['2010'] },
      { field: 'production_year', operator: 'lte', values: ['2012'] },
      { field: 'fuel', operator: 'is_empty', values: [] },
    ]);
  });

  it('says a group too large to count has to be narrowed', () => {
    const wide: CorrectionWideScope = { ...LIKE, count: null, too_broad: true };
    const page = render({ scopes: [{ kind: 'this_car' }, wide], choice: { option: wide, narrow: [] } });

    expect(page.options()[1]).toBe('All Volvo V70 cars with no drive type (too many to count)');
    expect(squash(page.host)).toContain('Too many cars to count. Narrow the group before checking it.');
  });
});

describe('CorrectionPreview: the check', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('shows progress with a native progress bar, a status line and Stop', async () => {
    const page = render({ check: 'starting' });
    expect(squash(page.host.querySelector('[role="status"]'))).toBe('Counting the cars…');

    page.set({
      check: job({ status: 'running', checked: 120, complete: false, counts: counts(), would_write: 0, can_apply: false }),
    });
    await page.settle();
    const bar = page.host.querySelector('progress') as HTMLProgressElement;
    expect([bar.value, bar.max]).toEqual([120, 212]);
    expect(squash(page.host.querySelector('[role="status"]'))).toBe('Checking 120 of 212 cars…');
    expect(page.host.querySelector('fieldset')).toBeNull();

    await page.click('Stop');
    expect(page.seen.events).toEqual(['stop']);
  });

  it('counts towards what one check may hold, with thousands separators', async () => {
    const page = render({
      check: job({ status: 'running', affected: 4275, cap: 1500, checked: 1200, complete: false }),
    });
    expect(squash(page.host.querySelector('[role="status"]'))).toBe('Checking 1,200 of 1,500 cars…');
    expect(squash(page.host)).toContain('This would touch 4,275 cars. At most 1,500 can be checked here.');
  });

  it('says the result in one sentence per count, zero only for lost matches, and takes focus', async () => {
    const page = render({ check: job() });
    await page.settle();

    const heading = page.host.querySelector('h5') as HTMLElement;
    expect(squash(heading)).toBe('What would change for the 212 cars');
    expect(document.activeElement).toBe(heading);
    expect(squash(page.host)).toContain('With Drive type fwd: All Volvo V70 cars with no drive type.');
    expect(page.sentences()).toEqual([
      '171 would resolve (they are unresolved now) Engine code against the new KType: 150 agree, 21 could not be compared.',
      '30 stay unresolved (22 still tie, 8 conflict)',
      '9 are resolved and stay on the same KType',
      '2 resolved cars would move to another KType',
      '0 resolved cars would lose their match',
      '1 car has a KType chosen by a person. The choice stays and will be marked for another look.',
      "1 car already has a person's correction for drive type. It is left as it is.",
    ]);
    // No outcome code and no field key reaches the screen.
    expect(squash(page.host)).not.toMatch(/still_unresolved|review_required|hard_conflict|drive_type/);
  });

  it('lists the cars behind a count when asked, plate and before → after', async () => {
    const page = render({ check: job() });
    const lists = [...page.host.querySelectorAll('details')];
    // A count of zero has no cars to see; neither has the note on chosen KTypes.
    expect(lists.length).toBe(5);
    expect(lists.every((list) => squash(list.querySelector('summary')) === 'See these cars')).toBe(true);
    expect(lists[0].querySelector('summary')?.getAttribute('aria-label')).toContain('171 would resolve');

    lists[0].open = true;
    lists[0].dispatchEvent(new Event('toggle'));
    await page.settle();
    expect(page.seen.seeCars).toEqual(['gained']);

    page.set({ lists: { gained: 'loading' } });
    await page.settle();
    expect(squash(lists[0])).toContain('Loading…');

    page.set({
      lists: {
        gained: [
          {
            vehicle_id: 'NOR-TEST0000000000000000000001',
            plate: 'TST001',
            before: { terminal: 'review_required', ktype: null },
            after: { terminal: 'resolved', ktype: '000010064' },
          },
          {
            vehicle_id: 'NOR-TEST0000000000000000000002',
            plate: null,
            before: { terminal: 'unmatched', ktype: null },
            after: { terminal: 'resolved', ktype: '000011508' },
          },
        ],
      },
    });
    await page.settle();
    expect([...lists[0].querySelectorAll('ul.cars li')].map(squash)).toEqual([
      'TST001 tie → KType 000010064',
      'NOR-TEST0000000000000000000002 no KType found → KType 000011508',
    ]);
    expect(squash(lists[0])).toContain('The first 2 of 171 are listed.');

    page.set({ lists: { gained: 'failed' } });
    await page.settle();
    expect(squash(lists[0].querySelector('[role="alert"]'))).toContain('These cars could not be listed.');
    (lists[0].querySelector('[role="alert"] button') as HTMLButtonElement).click();
    expect(page.seen.seeCars).toEqual(['gained', 'gained']);
  });
});

describe('CorrectionPreview: what may be done with the result', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('needs a reason to apply, and applies to the cars that are not harmed', async () => {
    const page = render({ check: job() });
    await page.settle();
    const apply = page.button('Apply to 210 cars') as HTMLButtonElement;
    const reason = page.host.querySelector('textarea') as HTMLTextAreaElement;
    const hint = page.host.querySelector(`#${apply.getAttribute('aria-describedby')}`);

    expect(apply.disabled).toBe(true);
    expect(squash(hint)).toBe('Give a reason to apply this to several cars.');
    expect(reason.getAttribute('aria-describedby')).toBe(apply.getAttribute('aria-describedby'));

    await page.type(reason, 'Seen in the registration papers');
    expect(page.fixture.componentInstance.reason()).toBe('Seen in the registration papers');
    expect(apply.disabled).toBe(false);
    expect(apply.hasAttribute('aria-describedby')).toBe(false);

    await page.click('Apply to 210 cars');
    expect(page.seen.apply).toEqual([false]);
    await page.click('Cancel');
    expect(page.seen.events).toEqual(['cancel']);
  });

  it('leaves the cars that would move or lose their KType out unless they are included', async () => {
    const page = render({
      reason: 'Seen',
      check: job({ counts: counts({ gained: 171, moved: 2, lost: 1, same: 9 }), would_write: 180 }),
    });
    await page.settle();

    const note = page.host.querySelector('.note') as HTMLElement;
    expect(squash(note.querySelector('p'))).toBe(
      '2 resolved cars would move to another KType and 1 resolved car would lose its match. They are left out unless you include them.',
    );
    expect(squash(note.querySelector('label'))).toBe('Include these 3 cars; I looked at them');
    expect(page.button('Apply to 180 cars')).toBeTruthy();

    (note.querySelector('input') as HTMLInputElement).click();
    await page.settle();
    await page.click('Apply to 183 cars');
    expect(page.seen.apply).toEqual([true]);

    // Ticked for that check only: the next one starts without them.
    page.set({ check: job({ preview_id: 'preview-0002', would_write: 180, counts: counts({ gained: 171, moved: 2, lost: 1, same: 9 }) }) });
    await page.settle();
    expect((page.host.querySelector('.note input') as HTMLInputElement).checked).toBe(false);
    expect(page.button('Apply to 180 cars')).toBeTruthy();
  });

  it('says one harmed car in the singular', async () => {
    const page = render({ check: job({ counts: counts({ gained: 3, moved: 1 }), would_write: 3 }) });
    await page.settle();
    const note = page.host.querySelector('.note') as HTMLElement;
    expect(squash(note.querySelector('p'))).toBe(
      '1 resolved car would move to another KType. It is left out unless you include it.',
    );
    expect(squash(note.querySelector('label'))).toBe('Include this car; I looked at it');
  });

  it('refuses to apply what harms as many cars as it fixes, and offers to narrow or propose', async () => {
    const page = render({
      choice: { option: LIKE, narrow: [] },
      reason: 'Seen',
      check: job({
        counts: counts({ gained: 4, moved: 3, lost: 2 }),
        would_write: 4,
        can_apply: false,
        blocked_by: ['harms_more_than_it_fixes'],
      }),
    });
    await page.settle();

    expect(squash(page.host.querySelector('p.note'))).toBe(
      'This would fix 4 cars and harm 5. It cannot be applied from here. Narrow the group, or save it as a proposal.',
    );
    expect([...page.host.querySelectorAll('button')].some((item) => squash(item).startsWith('Apply'))).toBe(false);
    expect(page.host.querySelector('.note input')).toBeNull();
    expect(page.host.querySelector('textarea')).toBeNull();

    await page.click('Save as a proposal');
    await page.click('Narrow it…');
    expect(page.seen.events).toEqual(['propose', 'narrow']);
  });

  it('cannot apply a check that did not reach every car: narrow it or save a proposal', async () => {
    const page = render({
      choice: { option: LIKE, narrow: [] },
      check: job({
        affected: 4275,
        checked: 500,
        complete: false,
        stopped_by: 'cap',
        counts: counts({ gained: 400, still_unresolved: 100, still_unresolved_by_terminal: { provisional: 100 } }),
        would_write: 500,
        can_apply: false,
        blocked_by: ['not_all_cars_checked'],
      }),
    });
    await page.settle();

    expect(squash(page.host.querySelector('h5'))).toBe('What would change for the 500 cars that were checked');
    const why = page.host.querySelector('p.note') as HTMLElement;
    expect(why.getAttribute('role')).toBe('status');
    expect(squash(why)).toBe(
      'This would touch 4,275 cars. At most 500 can be checked here, so it cannot be applied from here. Narrow the group, or save it as a proposal to be measured on all cars first.',
    );
    expect(page.sentences()[0]).toContain('400 would resolve');
    expect(page.sentences()[1]).toBe('100 stay unresolved (100 are not certain enough)');
    const narrow = page.button('Narrow it…') as HTMLButtonElement;
    expect(narrow.getAttribute('aria-describedby')).toBe(why.id);
    expect(page.button('Save as a proposal')).toBeTruthy();
    expect([...page.host.querySelectorAll('button')].some((item) => squash(item).startsWith('Apply'))).toBe(false);
  });

  it('says why for a stopped check, one out of time, a failed one and one with nothing to apply', async () => {
    const partial = { checked: 120, complete: false, can_apply: false, blocked_by: ['not_all_cars_checked'] };
    const page = render({ check: job({ ...partial, status: 'cancelled', stopped_by: 'stopped' }) });
    await page.settle();
    const why = () => squash(page.host.querySelector('p.note'));
    expect(why()).toBe(
      'The check was stopped after 120 of 212 cars, so it cannot be applied from here. Save it as a proposal to be measured on all cars first.',
    );

    page.set({ check: job({ ...partial, stopped_by: 'time_limit' }) });
    await page.settle();
    expect(why()).toContain('The check ran out of time after 120 of 212 cars, so it cannot be applied from here.');

    page.set({ check: job({ status: 'failed', checked: 3, complete: false, can_apply: false }) });
    await page.settle();
    expect(squash(page.host.querySelector('h5'))).toBe('The check could not be finished');
    expect(why()).toBe('Nothing was changed. Check again.');
    expect(page.sentences()).toEqual([]);
    expect(page.button('Save as a proposal')).toBeUndefined();

    page.set({
      check: job({
        counts: counts({ no_effect: 211, already_corrected: 1 }),
        would_write: 0,
        can_apply: false,
        blocked_by: ['nothing_to_apply'],
      }),
    });
    await page.settle();
    expect(why()).toBe('No car in this group would take the correction, so there is nothing to apply.');
    expect(page.sentences()).toEqual([
      '0 resolved cars would lose their match',
      "1 car already has a person's correction for drive type. It is left as it is.",
      '211 cars already have this value. Nothing changes for them.',
    ]);

    page.set({ check: job({ can_apply: false, blocked_by: ['preview_expired'] }) });
    await page.settle();
    expect(why()).toBe('The check is too old. Check again.');
  });

  it('turns everything off while an apply is on its way', async () => {
    const page = render({ check: job(), reason: 'Seen', saving: true });
    await page.settle();
    expect([...page.host.querySelectorAll('button')].every((item) => item.disabled)).toBe(true);
    expect((page.host.querySelector('textarea') as HTMLTextAreaElement).disabled).toBe(true);
  });
});

describe('CorrectionPreview: what was done, in words', () => {
  it('counts cars with thousands separators and in the singular', () => {
    expect(carCount(1)).toBe('1 car');
    expect(carCount(4275)).toBe('4,275 cars');
  });

  it('says what an apply wrote and what it left', () => {
    const answer: CorrectionDecisionResult = {
      decision_id: '11111111-1111-4111-8111-111111111111',
      status: 'applied',
      written: 210,
      skipped: { changed_since_check: 0, corrected_meanwhile: 0 },
      counts: counts({ gained: 171 }),
    };
    expect(appliedInWords(answer)).toBe('Applied to 210 cars. 171 now resolve.');
    expect(
      appliedInWords({
        ...answer,
        written: 1207,
        skipped: { changed_since_check: 2, corrected_meanwhile: 1 },
        written_by_outcome: { gained: 1 },
      }),
    ).toBe(
      'Applied to 1,207 cars. 1 now resolves. 2 changed since the check and were left as they are. 1 was corrected by a person meanwhile and was left as it is.',
    );
  });

  it('says what an undo took back and what it left', () => {
    const answer = {
      decision_id: '11111111-1111-4111-8111-111111111111',
      status: 'withdrawn' as const,
      withdrawn: 58,
      left_changed: 1,
    };
    expect(undoneInWords(answer)).toBe(
      'Undone for 58 cars. 1 was changed by a person since and was left as it is.',
    );
    expect(undoneInWords({ ...answer, withdrawn: 59, left_changed: 0 })).toBe('Undone for 59 cars.');
  });
});
