/**
 * Integration checks that each page renders rows from the real API.
 *
 * These deliberately hit a running backend rather than a mock: the point is to prove the
 * five pages render real data, not that a fixture round-trips. Set NS_API_BASE to aim at a
 * different backend. Every test skips (rather than fails) when the API is unreachable, so
 * the suite stays runnable without a database.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { beforeAll, describe, expect, it } from 'vitest';

/** These tests query a live backend over 6.5M rows; 5s is a unit-test budget. */
const INTEGRATION_TIMEOUT = 30_000;

import { API_BASE_URL } from '../core/api-config';
import { FilterState } from '../core/filter-state';
import { TsRecordsPage } from './ts-records/ts-records';
import { TecDocPage } from './tecdoc/tecdoc';
import { RulesPage } from './rules/rules';
import { ChunksPage } from './chunks/chunks';

// jsdom implements neither of these; Optimus's TabList and Table observe element size.
// Browsers provide them, so stubbing here only fills a test-environment gap.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
const globals = globalThis as Record<string, unknown>;
globals['ResizeObserver'] ??= ResizeObserverStub;
globals['IntersectionObserver'] ??= ResizeObserverStub;

declare const process: { env: Record<string, string | undefined> } | undefined;

const API_BASE =
  (typeof process !== 'undefined' ? process?.env['NS_API_BASE'] : undefined) ??
  'http://127.0.0.1:8010';

let apiUp = false;

async function settle(fixture: { whenStable: () => Promise<unknown> }, rounds = 6) {
  for (let i = 0; i < rounds; i += 1) {
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
}

function configure() {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(withFetch()),
      provideRouter([]),
      provideOptimus({ theme: { preset: Aura } }),
      { provide: API_BASE_URL, useValue: API_BASE },
    ],
  });
}

beforeAll(async () => {
  try {
    const response = await fetch(`${API_BASE}/health`, {
      signal: AbortSignal.timeout(4000),
    });
    apiUp = response.ok;
  } catch {
    apiUp = false;
  }
});

describe('pages render real data', () => {
  it('TS data filters the whole population and names its gaps', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(TsRecordsPage);
    fixture.detectChanges();
    await settle(fixture, 10);
    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('TS data');
    expect(host.querySelectorAll('tbody tr').length).toBeGreaterThan(0);

    // The gaps sidebar is what makes browsing lead somewhere: without it the screen is
    // a list, and the filter has nowhere to go. Asserted on the affordance rather than
    // its wording, which has already changed once.
    expect(host.querySelectorAll('.gap').length).toBeGreaterThan(0);
    expect(host.querySelectorAll('.gap__set').length).toBeGreaterThan(0);
  }, INTEGRATION_TIMEOUT);

  it('the gap view groups by shape, not by exact value', async () => {
    if (!apiUp) return;
    configure();
    const filter = TestBed.inject(FilterState);
    filter.reset();

    const fixture = TestBed.createComponent(TsRecordsPage);
    fixture.detectChanges();
    await settle(fixture, 12);
    const host = fixture.nativeElement as HTMLElement;

    const tabs = [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    const gapTab = tabs.find((tab) => (tab.textContent ?? '').includes('Where the gap'));
    expect(gapTab).toBeTruthy();
    gapTab?.click();
    fixture.detectChanges();
    await settle(fixture, 12);

    const groups = [...fixture.nativeElement.querySelectorAll('.group')];
    expect(groups.length).toBeGreaterThan(0);

    // Grouping by shape is the whole point: an exact-value list of this gap runs to
    // tens of thousands of rows, and one leading token covers a fifth of it. A group
    // must therefore stand for many distinct values, not one.
    const distinct = groups
      .map((group) => (group.textContent ?? '').match(/([\d,]+) distinct values/)?.[1])
      .filter((value): value is string => Boolean(value))
      .map((value) => Number(value.replace(/,/g, '')));
    expect(distinct.length).toBeGreaterThan(0);
    expect(Math.max(...distinct)).toBeGreaterThan(1);
  }, INTEGRATION_TIMEOUT);

  it('TS data resolves a field without leaving the screen', async () => {
    if (!apiUp) return;
    configure();
    // Browsing and resolving were two pages with a handoff between them; the handoff
    // was the tell that they are one workflow.
    const filter = TestBed.inject(FilterState);
    filter.reset();
    filter.addTerm('brand', 'TOYOTA');

    const fixture = TestBed.createComponent(TsRecordsPage);
    fixture.detectChanges();
    await settle(fixture, 12);
    const host = fixture.nativeElement as HTMLElement;

    const setValue = host.querySelector<HTMLButtonElement>('.gap__set');
    expect(setValue).toBeTruthy();
    setValue?.click();
    fixture.detectChanges();
    await settle(fixture, 8);

    const host2 = fixture.nativeElement as HTMLElement;
    expect(host2.querySelector('.resolver')).toBeTruthy();
    // The filter is the rule's predicate, so it reaches the statement unchanged.
    expect(host2.querySelector('.statement')?.textContent ?? '').toContain('TOYOTA');
  }, INTEGRATION_TIMEOUT);

  it('TecDoc lists promoted ktypes', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(TecDocPage);
    fixture.detectChanges();
    await settle(fixture, 10);
    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent ?? '').toContain('TecDoc');
    expect(host.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
  }, INTEGRATION_TIMEOUT);

  it('Rules lists the rule catalog', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(RulesPage);
    fixture.detectChanges();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent ?? '').toContain('Rules');
    expect(host.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
  });

  // Match review stays the TS-to-TecDoc blocker screen; unresolved-field rule authoring
  // lives on its own page and must not leak in here.
  it('Match review renders TS-to-TecDoc blocker patterns', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(ChunksPage);
    fixture.detectChanges();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('Match review');
    expect(text).toContain('blocker patterns');
    expect(text).not.toContain('Unresolved fields');
  }, INTEGRATION_TIMEOUT);
});
