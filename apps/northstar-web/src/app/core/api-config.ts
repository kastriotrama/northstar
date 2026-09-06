import { InjectionToken } from '@angular/core';

/**
 * Base URL of the FastAPI backend.
 *
 * Overridden at runtime by `window.__NORTHSTAR_API__` when present, so the same build can
 * point at a different API without a rebuild.
 */
export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => {
    const override = (globalThis as Record<string, unknown>)['__NORTHSTAR_API__'];
    return typeof override === 'string' && override.length > 0
      ? override
      : 'http://127.0.0.1:8010';
  },
});
