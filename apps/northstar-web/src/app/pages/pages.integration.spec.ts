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

import { API_BASE_URL } from '../core/api-config';
import { CoveragePage } from './coverage/coverage';
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
  it('TS records lists raw staging rows', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(TsRecordsPage);
    fixture.detectChanges();
    await settle(fixture);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('tbody tr');
    expect(text).toContain('TS records');
    expect(rows.length).toBeGreaterThan(0);
  });

  it('Unresolved fields ranks populations by how many cars they block', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(CoveragePage);
    fixture.detectChanges();
    await settle(fixture, 10);
    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent ?? '').toContain('Unresolved fields');

    // The worklist is the entry point, and it is ranked: the leverage ordering is the
    // reason the screen is population-first, so a wrongly ordered list is a real failure.
    const rows = Array.from(host.querySelectorAll('.pop'));
    expect(rows.length).toBeGreaterThan(0);
    const counts = rows.map((row) =>
      Number((row.querySelector('.pop__count')?.textContent ?? '').replace(/[^0-9]/g, '')),
    );
    expect(counts[0]).toBeGreaterThan(0);
    expect([...counts]).toEqual([...counts].sort((a, b) => b - a));
  });

  it('TecDoc lists promoted ktypes', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(TecDocPage);
    fixture.detectChanges();
    await settle(fixture, 10);
    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent ?? '').toContain('TecDoc');
    expect(host.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
  });

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
  });
});
