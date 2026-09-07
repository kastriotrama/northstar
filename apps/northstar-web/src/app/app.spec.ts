import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { App } from './app';

describe('App', () => {
  it('offers one screen per concern in the pipeline', async () => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    const host = fixture.nativeElement as HTMLElement;
    const labels = [...host.querySelectorAll('.shell__link')].map((link) =>
      (link.textContent ?? '').trim(),
    );

    // Four, not five: browsing a population and saying what it means are one workflow,
    // and the page count is the thing most likely to creep back up.
    expect(labels.length).toBe(4);
    expect(labels.some((label) => label.startsWith('TS data'))).toBe(true);
    expect(labels.some((label) => label.startsWith('Unresolved'))).toBe(false);
  });
});
