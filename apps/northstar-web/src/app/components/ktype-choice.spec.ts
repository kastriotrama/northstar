/**
 * The choice panel's states: what is stored, when it needs another look, what a
 * confirm step asks, and that the controls are native and labelled.
 */

import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import type { KTypeCandidate, KTypeChoiceState, VehicleMatchLookup } from '../core/models';
import { KTypeChoice, type PendingChoice } from './ktype-choice';

function candidate(ktype: string, overrides: Partial<KTypeCandidate> = {}): KTypeCandidate {
  return {
    ktype,
    candidate_only: false,
    confidence: 0.98,
    manufacturer: 'VOLVO',
    model: 'V70 III (135)',
    year_from: 2010,
    year_to: 2015,
    fuels: ['diesel'],
    engine_codes: ['D 5204 T2'],
    displacement_cc: 1984,
    power_kw: 120,
    drive_type: 'fwd',
    bodyworks: ['estate'],
    matched_fields: [],
    missing_fields: [],
    conflicting_fields: [],
    compatible: true,
    ...overrides,
  };
}

function choice(overrides: Partial<KTypeChoiceState> = {}): KTypeChoiceState {
  return {
    status: 'chosen',
    choice_id: '11111111-1111-4111-8111-111111111111',
    ktype: '000010064',
    reviewer: 'Anna',
    reason: 'Checked the registration papers',
    created_at: '2026-10-01T09:30:00Z',
    catalog_batch: 'tecdoc-v5',
    automatic_terminal: 'review_required',
    automatic_ktype: null,
    chosen_candidate: candidate('000010064'),
    needs_review: false,
    stale_reasons: [],
    changed_inputs: [],
    history_count: 1,
    ...overrides,
  };
}

function lookup(overrides: Partial<VehicleMatchLookup> = {}): VehicleMatchLookup {
  return {
    vehicle_id: 'NOR-TEST',
    source_record_id: 1,
    plate: null,
    vin: null,
    catalog_batch: 'tecdoc-v5',
    terminal: 'review_required',
    bucket: 'several',
    confidence: 0.9,
    top_ktype: '000010064',
    reason_codes: [],
    verdict: null,
    rule_filled: [],
    overlaid_fields: {},
    inputs: null,
    candidates: [
      candidate('000010064'),
      candidate('000059385', { conflicting_fields: ['power_kw'], compatible: false }),
    ],
    candidate_limit: 5,
    separating_fields: [],
    missing_separating_fields: [],
    decision_trace: [],
    other_vehicle_ids: [],
    evidence_fingerprint: 'a'.repeat(64),
    choice: null,
    ...overrides,
  };
}

function pendingChoice(action: 'choose' | 'none' | 'withdraw', ktype: string | null = null): PendingChoice {
  return {
    operationId: 'op',
    needsConfirm: true,
    body: { operation_id: 'op', action, ktype, reviewer: 'Bea', supersedes_choice_id: null },
  };
}

function render(inputs: Record<string, unknown>) {
  const fixture = TestBed.createComponent(KTypeChoice);
  for (const [name, value] of Object.entries({ reviewer: 'Bea', ...inputs })) {
    fixture.componentRef.setInput(name, value);
  }
  fixture.detectChanges();
  const host = fixture.nativeElement as HTMLElement;
  const text = () => (host.textContent ?? '').replace(/\s+/g, ' ');
  const button = (label: string) =>
    [...host.querySelectorAll('button')].find((item) => item.textContent?.trim() === label);
  return { fixture, host, text, button };
}

describe('KTypeChoice', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('shows a chosen KType as chosen by a person, with who, when and why', () => {
    const { text, button, host } = render({ lookup: lookup({ choice: choice() }) });

    expect(text()).toContain('KType 000010064 · chosen by a person — Anna, 2026-10-01');
    expect(text()).toContain('“Checked the registration papers”');
    expect(button('Withdraw choice')).toBeTruthy();
    expect(host.querySelector('.stale')).toBeNull();
    expect(text()).toContain('Choice history (1)');
  });

  it('shows a recorded “none of these”', () => {
    const { text, button } = render({
      lookup: lookup({ choice: choice({ status: 'none', ktype: null, chosen_candidate: null, reason: null }) }),
    });

    expect(text()).toContain('No KType · “none of these” recorded by Anna, 2026-10-01');
    expect(button('Withdraw choice')).toBeTruthy();
  });

  it('says a withdrawn choice no longer applies, and offers nothing to withdraw', () => {
    const { text, button } = render({
      lookup: lookup({ choice: choice({ status: 'withdrawn', ktype: null, chosen_candidate: null }) }),
    });

    expect(text()).toContain(
      'An earlier choice was withdrawn by Anna on 2026-10-01. The automatic result applies.',
    );
    expect(button('Withdraw choice')).toBeUndefined();
  });

  it('puts each reason a choice needs another look in words', () => {
    const { host, text, button } = render({
      lookup: lookup({
        catalog_batch: 'tecdoc-v6',
        choice: choice({
          needs_review: true,
          stale_reasons: ['catalog_batch_changed', 'evidence_changed'],
          changed_inputs: [{ field: 'power_kw', then: 110, now: 103 }],
        }),
      }),
    });

    const block = host.querySelector('.stale');
    expect(block?.getAttribute('role')).toBe('status');
    expect(text()).toContain('This choice needs another look');
    expect(text()).toContain('The TecDoc catalog changed since the choice (was tecdoc-v5, now tecdoc-v6).');
    expect(text()).toContain("The car's data changed since the choice: power 110 → 103.");
    expect(text()).toContain('Nothing was changed. The choice stands until someone changes it.');
    // The KType is still offered, so it can be kept on today's evidence.
    expect(button('Keep this choice')).toBeTruthy();
  });

  it('shows the stored candidate, and no “keep”, when the chosen KType is gone', () => {
    const { host, text, button } = render({
      lookup: lookup({
        candidates: [candidate('000059385')],
        choice: choice({
          needs_review: true,
          stale_reasons: ['ktype_not_a_candidate', 'ktype_not_in_catalog'],
        }),
      }),
    });

    expect(text()).toContain("KType 000010064 is no longer among this car's candidates.");
    expect(text()).toContain('KType 000010064 is no longer in the TecDoc catalog.');
    expect(host.querySelector('.gone')?.textContent).toContain('chosen · no longer a candidate');
    expect(host.querySelector('.gone')?.textContent).toContain('120 kW');
    expect(button('Keep this choice')).toBeUndefined();
    expect(button('Withdraw choice')).toBeTruthy();
  });

  it('flags a “none of these” when new KTypes are offered, and lets it be kept', () => {
    const { text, button } = render({
      lookup: lookup({
        choice: choice({
          status: 'none',
          ktype: null,
          chosen_candidate: null,
          needs_review: true,
          stale_reasons: ['new_candidates'],
        }),
      }),
    });

    expect(text()).toContain('KTypes are offered now that were not offered when “none of these” was recorded.');
    expect(button('Keep “none of these”')).toBeTruthy();
  });

  it('asks before overriding the matcher, choosing a ruled-out KType or replacing a choice', () => {
    const { text, host } = render({
      lookup: lookup({ terminal: 'resolved', choice: choice() }),
      pending: pendingChoice('choose', '000059385'),
    });

    expect(text()).toContain(
      "The matcher resolved this car to KType 000010064. Your choice of KType 000059385 will be shown instead; the matcher's result stays visible.",
    );
    expect(text()).toContain('KType 000059385 conflicts with this car on power. Choose it anyway?');
    expect(text()).toContain("Replace Anna's choice of KType 000010064 with KType 000059385?");
    expect(host.querySelector('.confirm')?.getAttribute('role')).toBe('group');
  });

  it('asks before recording “none of these” and before withdrawing, and emits the answer', () => {
    const none = render({ lookup: lookup(), pending: pendingChoice('none') });
    expect(none.text()).toContain('Record that none of these KTypes is this car?');
    let confirmed = 0;
    let cancelled = 0;
    none.fixture.componentInstance.confirm.subscribe(() => confirmed++);
    none.fixture.componentInstance.cancel.subscribe(() => cancelled++);
    none.button('Confirm')?.click();
    none.button('Cancel')?.click();
    expect([confirmed, cancelled]).toEqual([1, 1]);

    const withdraw = render({ lookup: lookup({ choice: choice() }), pending: pendingChoice('withdraw') });
    expect(withdraw.text()).toContain('Withdraw the choice? The automatic result will apply again.');
  });

  it('needs a name before anything can be recorded', () => {
    const { fixture, host, button } = render({ lookup: lookup({ choice: choice() }), reviewer: '  ' });

    const none = button('None of these');
    expect(none?.disabled).toBe(true);
    expect(button('Withdraw choice')?.disabled).toBe(true);
    const hint = host.querySelector(`#${none?.getAttribute('aria-describedby')}`);
    expect(hint?.textContent).toContain('Enter your name to record a choice.');

    const name = host.querySelector('input') as HTMLInputElement;
    name.value = 'Bea';
    name.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    expect(fixture.componentInstance.reviewer()).toBe('Bea');
    expect(button('None of these')?.disabled).toBe(false);
    expect(button('None of these')?.hasAttribute('aria-describedby')).toBe(false);
  });

  it('ties the hint to withdraw and keep, and the confirm button to what it confirms', () => {
    const stale = choice({ needs_review: true, stale_reasons: ['catalog_batch_changed'] });
    const unnamed = render({ lookup: lookup({ choice: stale }), reviewer: '' });
    for (const label of ['Withdraw choice', 'Keep this choice']) {
      const control = unnamed.button(label);
      expect(control?.disabled, label).toBe(true);
      const hint = unnamed.host.querySelector(`#${control?.getAttribute('aria-describedby')}`);
      expect(hint?.textContent, label).toContain('Enter your name to record a choice.');
    }

    TestBed.resetTestingModule();
    const asking = render({
      lookup: lookup({ choice: choice() }),
      pending: pendingChoice('withdraw'),
    });
    expect(asking.button('Withdraw choice')?.hasAttribute('aria-describedby')).toBe(false);
    const confirm = asking.button('Confirm');
    const lines = asking.host.querySelector(`#${confirm?.getAttribute('aria-describedby')}`);
    expect(lines?.textContent).toContain('Withdraw the choice? The automatic result will apply again.');
  });

  it('says when the history cannot be loaded and offers to try again', () => {
    const { fixture, host, text, button } = render({
      lookup: lookup({ choice: choice() }),
      historyError: true,
    });
    const opened: string[] = [];
    fixture.componentInstance.historyOpened.subscribe(() => opened.push('opened'));
    fixture.componentInstance.historyClosed.subscribe(() => opened.push('closed'));

    expect(text()).toContain('The history could not be loaded.');
    expect(text()).not.toContain('Loading…');
    button('Try again')?.click();
    const details = host.querySelector('details') as HTMLDetailsElement;
    details.open = false;
    details.dispatchEvent(new Event('toggle'));

    expect(opened).toEqual(['opened', 'closed']);
  });

  it('uses native, labelled controls', () => {
    const { host } = render({ lookup: lookup() });

    const labels = [...host.querySelectorAll('label')].map((label) => ({
      text: label.textContent?.trim(),
      control: label.querySelector('input, textarea')?.tagName,
    }));
    expect(labels).toEqual([
      { text: 'Your name', control: 'INPUT' },
      { text: 'Reason (optional)', control: 'TEXTAREA' },
    ]);
    expect((host.querySelector('input') as HTMLInputElement).required).toBe(true);
  });

  it('words the button for a car with no candidates', () => {
    const { button } = render({ lookup: lookup({ candidates: [], bucket: 'none' }) });
    expect(button('Record that no KType fits')).toBeTruthy();
  });

  it('says saving, saved, and failed with a way to try again', () => {
    const saving = render({ lookup: lookup({ choice: choice() }), saving: true });
    expect(saving.host.querySelector('[role="status"]')?.textContent).toContain('Saving…');
    expect(saving.button('Withdraw choice')?.disabled).toBe(true);
    expect(saving.button('None of these')?.disabled).toBe(true);

    const saved = render({ lookup: lookup(), notice: 'Saved. KType 000010064 chosen for this car.' });
    expect(saved.host.querySelector('.saved')?.getAttribute('role')).toBe('status');

    const failed = render({ lookup: lookup(), error: { message: 'Not saved. Try again.', retry: true } });
    expect(failed.host.querySelector('[role="alert"]')?.textContent).toContain('Not saved. Try again.');
    let retried = 0;
    failed.fixture.componentInstance.retry.subscribe(() => retried++);
    failed.button('Try again')?.click();
    expect(retried).toBe(1);

    const refused = render({ lookup: lookup(), error: { message: 'There is no choice to withdraw.', retry: false } });
    expect(refused.button('Try again')).toBeUndefined();
  });

  it('loads the history when first opened and reads each entry in words', () => {
    const { fixture, host, text } = render({ lookup: lookup({ choice: choice({ history_count: 2 }) }) });
    let opened = 0;
    fixture.componentInstance.historyOpened.subscribe(() => opened++);
    const details = host.querySelector('details') as HTMLDetailsElement;
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    expect(opened).toBe(1);

    const entry = {
      choice_id: 'b',
      action: 'choose' as const,
      ktype: '000010064',
      reviewer: 'Anna',
      reason: 'papers',
      created_at: '2026-10-01T09:30:00Z',
      supersedes_choice_id: 'a',
      catalog_batch: 'tecdoc-v5',
      automatic_terminal: 'review_required',
      automatic_ktype: null,
      code_version: 'abc',
    };
    fixture.componentRef.setInput('history', {
      vehicle_id: 'NOR-TEST',
      current_choice_id: 'b',
      entries: [entry, { ...entry, choice_id: 'a', action: 'none', ktype: null, reason: null, supersedes_choice_id: null }],
    });
    fixture.detectChanges();

    expect(text()).toContain('Anna chose KType 000010064 — “papers” · matcher then: review required');
    expect(text()).toContain('Anna recorded none of these');
  });
});
