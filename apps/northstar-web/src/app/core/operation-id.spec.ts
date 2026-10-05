import { afterEach, describe, expect, it, vi } from 'vitest';

import { newOperationId } from './operation-id';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('newOperationId', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('is a version 4 UUID, different every time', () => {
    const first = newOperationId();
    expect(first).toMatch(UUID_V4);
    expect(newOperationId()).not.toBe(first);
  });

  it('still makes one on a page served over plain http, where randomUUID does not exist', () => {
    // What a browser exposes outside a secure context: getRandomValues, no randomUUID.
    const real = globalThis.crypto;
    vi.stubGlobal('crypto', {
      getRandomValues: <T extends ArrayBufferView<ArrayBuffer>>(array: T): T =>
        real.getRandomValues(array),
    });

    const ids = new Set(Array.from({ length: 50 }, () => newOperationId()));

    expect(ids.size).toBe(50);
    for (const id of ids) expect(id).toMatch(UUID_V4);
  });
});
