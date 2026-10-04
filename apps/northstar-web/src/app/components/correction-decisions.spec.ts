/**
 * The overview of corrections applied to several cars: what each one is, where it stands,
 * and undoing one as a whole without first finding a car that belongs to it.
 * Mocked: the list and the undo are fixtures; the point is what is asked and how it reads.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type { CorrectionDecisionSummary } from '../core/models';
import { CorrectionDecisions } from './correction-decisions';

const LIST = 'http://api.test/v1/vehicle-corrections/decisions';

function decision(overrides: Partial<CorrectionDecisionSummary> = {}): CorrectionDecisionSummary {
  return {
    decision_id: 'd-1',
    status: 'applied',
    field: 'drive_type',
    field_label: 'Drive type',
    action: 'set',
    value: 'fwd',
    scope_label: 'All Volvo V70 cars with no drive type',
    manufacturer: 'VOLVO',
    model_family: 'V70',
    reviewer: 'Anna',
    reason: 'checked the papers',
    created_at: '2026-10-01T09:30:00Z',
    member_count: 210,
    measurement: { affected: 212, checked: 212, complete: true, gained: 171, lost: 0, moved: 0, worse: 0 },
    events: [
      { event_id: 'd-1', event: 'apply', reviewer: 'Anna', reason: 'checked the papers', created_at: '2026-10-01T09:30:00Z' },
    ],
    ...overrides,
  };
}

async function render(list: CorrectionDecisionSummary[] | null = [decision()], name = 'Bo') {
  localStorage.clear();
  if (name) localStorage.setItem('match-review-reviewer', name);
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(CorrectionDecisions);
  fixture.detectChanges();
  const request = TestBed.inject(HttpTestingController).expectOne((req) => req.url === LIST);
  if (list) request.flush({ decisions: list });
  else request.flush('boom', { status: 500, statusText: 'Server Error' });
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return fixture;
}

/** The element's words, with a space where one piece of text ends and the next begins. */
function text(element: Element): string {
  const parts: string[] = [];
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const piece = (walker.currentNode.textContent ?? '').replace(/\s+/g, ' ').trim();
    if (piece) parts.push(piece);
  }
  return parts.join(' ');
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
  const found = [...host.querySelectorAll<HTMLButtonElement>('button')].find((item) =>
    text(item).startsWith(label),
  );
  if (!found) throw new Error(`no button "${label}"`);
  return found;
}

function type(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event('input'));
}

describe('CorrectionDecisions', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('says what was decided, for which cars, by whom, why, and what its check found', async () => {
    const host = (await render()).nativeElement as HTMLElement;
    const card = text(host.querySelector('article') as Element);

    expect(card).toContain('Drive type set to fwd');
    expect(card).toContain('Applied to 210 cars');
    expect(card).toContain('All Volvo V70 cars with no drive type');
    expect(card).toContain('Anna, 2026-10-01');
    expect(card).toContain('— “checked the papers”');
    expect(card).toContain('The check looked at 212 cars: 171 would resolve, none would lose or change its match.');
  });

  it('reads stored values and the other states in words', async () => {
    const host = (
      await render([
        decision({ decision_id: 'a', field_label: 'Electrification', value: 'plug_in_hybrid' }),
        decision({ decision_id: 'b', field_label: 'Engine code', action: 'ignore', value: null }),
        decision({ decision_id: 'c', field_label: 'Fuel', value: 'petrol,electricity', status: 'proposed', member_count: 0 }),
        decision({
          decision_id: 'd',
          status: 'withdrawn',
          measurement: { checked: 9, gained: 4, lost: 1, moved: 2, worse: 0 },
          events: [
            { event_id: 'd', event: 'apply', reviewer: 'Anna', reason: 'x', created_at: '2026-10-01T09:30:00Z' },
            { event_id: 'e', event: 'withdraw', reviewer: 'Bo', reason: 'wrong group', created_at: '2026-10-02T08:00:00Z' },
          ],
        }),
      ])
    ).nativeElement as HTMLElement;
    const cards = [...host.querySelectorAll('article')].map(text);

    expect(cards[0]).toContain('Electrification set to plug in hybrid');
    expect(cards[1]).toContain('Engine code marked as wrong');
    expect(cards[2]).toContain('Fuel set to petrol, electricity');
    expect(cards[2]).toContain('Proposal · no car changed');
    expect(cards[3]).toContain('Undone');
    expect(cards[3]).toContain('Undone by Bo on 2026-10-02');
    expect(cards[3]).toContain('— “wrong group”');
    expect(cards[3]).toContain('3 would lose or change their match or get harder to match');
    // Only a decision that still stands can be undone.
    expect(host.querySelectorAll('button.undo').length).toBe(2);
  });

  it('says so when nothing was decided yet, and when the list cannot be loaded', async () => {
    const empty = (await render([])).nativeElement as HTMLElement;
    expect(text(empty)).toContain('No correction has been applied to several cars yet.');

    TestBed.resetTestingModule();
    const fixture = await render(null);
    const failed = fixture.nativeElement as HTMLElement;
    expect(text(failed.querySelector('[role="alert"]') as Element)).toContain('The decisions could not be loaded.');

    button(failed, 'Try again').click();
    TestBed.inject(HttpTestingController).expectOne((req) => req.url === LIST).flush({ decisions: [decision()] });
    fixture.detectChanges();
    expect(text(failed)).toContain('Drive type set to fwd');
  });

  it('needs a name before a decision can be undone', async () => {
    const fixture = await render([decision()], '');
    const host = fixture.nativeElement as HTMLElement;
    const undo = host.querySelector('button.undo') as HTMLButtonElement;

    expect(undo.disabled).toBe(true);
    expect(undo.getAttribute('aria-describedby')).toBe('decisions-name-hint');
    expect(text(host)).toContain('Enter your name to undo a decision.');

    type(host.querySelector('label.name input') as HTMLInputElement, 'Bo');
    fixture.detectChanges();
    expect((host.querySelector('button.undo') as HTMLButtonElement).disabled).toBe(false);
  });

  it('undoes a decision as one, after a confirmation with a reason', async () => {
    const fixture = await render();
    const host = fixture.nativeElement as HTMLElement;
    let changed = 0;
    fixture.componentInstance.changed.subscribe(() => changed++);

    const undo = host.querySelector('button.undo') as HTMLButtonElement;
    expect(undo.getAttribute('aria-label')).toBe('Undo for all 210 cars: Drive type set to fwd');
    undo.click();
    fixture.detectChanges();
    const confirm = host.querySelector('.confirm') as HTMLElement;
    expect(text(confirm)).toContain('Undo this for all 210 cars? Each goes back to its own data.');
    // No reason yet: it cannot be sent.
    expect(button(confirm, 'Confirm').disabled).toBe(true);
    expect(text(confirm)).toContain('Give a reason to undo this for several cars.');

    type(confirm.querySelector('input') as HTMLInputElement, ' wrong group ');
    fixture.detectChanges();
    button(host.querySelector('.confirm') as HTMLElement, 'Confirm').click();
    const http = TestBed.inject(HttpTestingController);
    const request = http.expectOne(`${LIST}/d-1/withdraw`);
    expect(request.request.body).toMatchObject({ reviewer: 'Bo', reason: 'wrong group' });
    expect(request.request.body.operation_id).toMatch(/^[0-9a-f-]{36}$/);
    request.flush({ decision_id: 'd-1', status: 'withdrawn', withdrawn: 209, left_changed: 1 });
    fixture.detectChanges();

    expect(text(host.querySelector('[role="status"]') as Element)).toBe(
      'Undone for 209 cars. 1 was changed by a person since and was left as it is.',
    );
    expect(changed).toBe(1);
    // The list is read again, so the decision shows where it stands now.
    http.expectOne((req) => req.url === LIST).flush({ decisions: [decision({ status: 'withdrawn' })] });
    fixture.detectChanges();
    expect(host.querySelector('button.undo')).toBeNull();
  });

  it('keeps the same operation id when an undo is sent again after a server error', async () => {
    const fixture = await render();
    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('button.undo') as HTMLButtonElement).click();
    fixture.detectChanges();
    type(host.querySelector('.confirm input') as HTMLInputElement, 'wrong group');
    fixture.detectChanges();
    button(host.querySelector('.confirm') as HTMLElement, 'Confirm').click();
    const http = TestBed.inject(HttpTestingController);
    const first = http.expectOne(`${LIST}/d-1/withdraw`);
    first.flush({ detail: { code: 'vehicles_busy', message: 'busy' } }, { status: 503, statusText: 'Busy' });
    fixture.detectChanges();

    expect(text(host.querySelector('.confirm [role="alert"]') as Element)).toContain(
      'Not undone. Someone else is changing some of these cars right now. Try again.',
    );
    button(host.querySelector('.confirm [role="alert"]') as HTMLElement, 'Try again').click();
    const second = http.expectOne(`${LIST}/d-1/withdraw`);
    expect(second.request.body).toEqual(first.request.body);
  });

  it('reads the list again when the decision was already undone by someone else', async () => {
    const fixture = await render();
    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('button.undo') as HTMLButtonElement).click();
    fixture.detectChanges();
    type(host.querySelector('.confirm input') as HTMLInputElement, 'wrong group');
    fixture.detectChanges();
    button(host.querySelector('.confirm') as HTMLElement, 'Confirm').click();
    const http = TestBed.inject(HttpTestingController);
    http
      .expectOne(`${LIST}/d-1/withdraw`)
      .flush({ detail: { code: 'decision_changed', message: 'changed' } }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();

    expect(text(host)).toContain('This decision was already undone. The list was read again.');
    http.expectOne((req) => req.url === LIST);
  });

  it('cancels an undo without sending anything', async () => {
    const fixture = await render();
    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('button.undo') as HTMLButtonElement).click();
    fixture.detectChanges();
    button(host.querySelector('.confirm') as HTMLElement, 'Cancel').click();
    fixture.detectChanges();

    expect(host.querySelector('.confirm')).toBeNull();
    TestBed.inject(HttpTestingController).expectNone((req) => req.url.endsWith('/withdraw'));
  });
});
