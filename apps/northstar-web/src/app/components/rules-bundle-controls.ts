import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { catchError, of } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { InputTextModule } from '@openng/optimus-ui/inputtext';

import { Api } from '../core/api';
import type { RulesBundleImportResult } from '../core/models';

const LIVE_URL_STORAGE_KEY = 'rules-bundle-live-url';
const SYNC_TOKEN_STORAGE_KEY = 'rules-bundle-sync-token';

/**
 * Sync every manually-authored rule (TecDoc gap rulings + TS resolution rules)
 * straight against a live server -- no file ever touches disk.
 *
 * The DB is always the source of truth for what's live. Pull/Push run the same
 * round trip server-to-server against whatever URL is typed in below (remembered
 * per browser), so three people working at once -- two local, one live -- can
 * move rules around directly. A genuine edit collision (both sides changed the
 * same TecDoc rule) is never silently overwritten: the older side's write is
 * refused and reported back as a conflict -- see `tecdoc_conflicts` on the result.
 *
 * Shared between `/tecdoc` (where a rule is usually first written) and `/rules`
 * (which already browses both sources together) so either screen can move rules
 * without duplicating this logic.
 */
@Component({
  selector: 'ns-rules-bundle-controls',
  imports: [FormsModule, ButtonModule, InputTextModule],
  template: `
    <div class="rules-bundle">
      <div class="rules-bundle__row">
        <input
          pInputText
          type="text"
          placeholder="https://live.example.com"
          [ngModel]="liveUrl()"
          (ngModelChange)="onLiveUrlChange($event)"
          style="min-width: 260px"
        />
        <input
          pInputText
          type="password"
          placeholder="sync token"
          [ngModel]="syncToken()"
          (ngModelChange)="onSyncTokenChange($event)"
          style="min-width: 160px"
        />
        <p-button
          label="Pull from live"
          size="small"
          [outlined]="true"
          [disabled]="!liveUrl()"
          [loading]="pulling()"
          (onClick)="pull()"
        />
        <p-button
          label="Push to live"
          size="small"
          [outlined]="true"
          [disabled]="!liveUrl()"
          [loading]="pushing()"
          (onClick)="push()"
        />
      </div>
      @if (message(); as text) {
        <span class="rules-bundle__hint">{{ text }}</span>
      }
      @if (conflictMessage(); as text) {
        <span class="rules-bundle__hint rules-bundle__hint--warn">{{ text }}</span>
      }
      @if (error(); as text) {
        <span class="rules-bundle__hint rules-bundle__hint--error">{{ text }}</span>
      }
    </div>
  `,
  styles: `
    .rules-bundle {
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }
    .rules-bundle__row {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
    }
    .rules-bundle__hint {
      font-size: 0.78rem;
      color: var(--p-text-muted-color);
    }
    .rules-bundle__hint--warn {
      color: #7a5300;
    }
    .rules-bundle__hint--error {
      color: #8a2020;
    }
  `,
})
export class RulesBundleControls {
  private readonly api = inject(Api);

  protected readonly pulling = signal(false);
  protected readonly pushing = signal(false);
  protected readonly liveUrl = signal(RulesBundleControls.remembered(LIVE_URL_STORAGE_KEY));
  protected readonly syncToken = signal(RulesBundleControls.remembered(SYNC_TOKEN_STORAGE_KEY));
  protected readonly message = signal<string | null>(null);
  protected readonly conflictMessage = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);

  protected onLiveUrlChange(value: string): void {
    this.liveUrl.set(value);
    RulesBundleControls.remember(LIVE_URL_STORAGE_KEY, value);
  }

  protected onSyncTokenChange(value: string): void {
    this.syncToken.set(value);
    RulesBundleControls.remember(SYNC_TOKEN_STORAGE_KEY, value);
  }

  private static remembered(key: string): string {
    try {
      return localStorage.getItem(key) ?? '';
    } catch {
      return '';
    }
  }

  private static remember(key: string, value: string): void {
    try {
      localStorage.setItem(key, value);
    } catch {
      // Private browsing or storage disabled -- the field still works for this session.
    }
  }

  protected pull(): void {
    const url = this.liveUrl().trim();
    if (!url) {
      return;
    }
    this.pulling.set(true);
    this.error.set(null);
    this.conflictMessage.set(null);
    this.api
      .pullResolutionRules(url, this.syncToken().trim() || undefined)
      .pipe(
        catchError((err: unknown) => {
          this.error.set(RulesBundleControls.describe(err, 'Could not pull from that server.'));
          return of(null);
        }),
        takeUntilDestroyed(),
      )
      .subscribe((result) => {
        this.pulling.set(false);
        if (!result) {
          return;
        }
        this.reportImportResult(result, 'Pulled');
      });
  }

  protected push(): void {
    const url = this.liveUrl().trim();
    if (!url) {
      return;
    }
    this.pushing.set(true);
    this.error.set(null);
    this.conflictMessage.set(null);
    this.api
      .pushResolutionRules(url, this.syncToken().trim() || undefined)
      .pipe(
        catchError((err: unknown) => {
          this.error.set(RulesBundleControls.describe(err, 'Could not push to that server.'));
          return of(null);
        }),
        takeUntilDestroyed(),
      )
      .subscribe((result) => {
        this.pushing.set(false);
        if (!result) {
          return;
        }
        this.reportImportResult(result, 'Pushed');
      });
  }

  private reportImportResult(result: RulesBundleImportResult, verb: string): void {
    this.message.set(
      `${verb}: ${result.tecdoc_single_target + result.tecdoc_compatible} TecDoc rule(s), ` +
        `${result.ts_created} new TS rule(s) (${result.ts_already_present} already present, ` +
        `${result.ts_skipped_invalid} skipped).`,
    );
    if (result.tecdoc_conflicts.length > 0) {
      this.conflictMessage.set(
        `${result.tecdoc_conflicts.length} rule(s) were NOT applied -- the other side's copy ` +
          `is older than what's already here. Review before overwriting by hand.`,
      );
    }
  }

  private static describe(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } } | null)?.error?.detail;
    return typeof detail === 'string' && detail.length > 0 ? detail : fallback;
  }
}
