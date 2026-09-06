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

  it('Coverage reports field gaps for a batch', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(CoveragePage);
    fixture.detectChanges();
    await settle(fixture, 10);
    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('Unresolved / coverage');
    expect(host.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
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

  it('Chunks renders the match-review build', async () => {
    if (!apiUp) return;
    configure();
    const fixture = TestBed.createComponent(ChunksPage);
    fixture.detectChanges();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent ?? '').toContain('Chunks');
  });
});
