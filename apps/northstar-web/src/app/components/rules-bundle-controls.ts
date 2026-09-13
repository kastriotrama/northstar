import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { catchError, of } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { InputTextModule } from '@openng/optimus-ui/inputtext';

import { Api } from '../core/api';
import type { RulesBundleExport, RulesBundleImportResult } from '../core/models';

const LIVE_URL_STORAGE_KEY = 'rules-bundle-live-url';

/**
 * Export/import every manually-authored rule (TecDoc gap rulings + TS resolution
 * rules) as one JSON file, plus sync straight against a live server.
 *
 * The DB is always the source of truth for what's live; export/import is a carrier
 * between databases -- a rule added on one machine reaches another with a click
 * instead of running `scripts/export_resolution_rules.py` / `load_resolution_rules.py`
 * by hand. Pull/Push do the same round trip server-to-server against whatever URL is
 * typed in below (remembered per browser), so three people working at once -- two
 * local, one live -- can move rules around without a file ever touching disk. A
 * genuine edit collision (both sides changed the same TecDoc rule) is never silently
 * overwritten: the older side's write is refused and reported back as a conflict --
 * see `tecdoc_conflicts` on the result.
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
        <p-button
          label="Export rules"
          size="small"
          [outlined]="true"
          [loading]="exporting()"
          (onClick)="export()"
        />
        <p-button
          label="Import rules"
          size="small"
          [outlined]="true"
          [loading]="importing()"
          (onClick)="fileInput.click()"
        />
        <input
          #fileInput
          type="file"
          accept="application/json"
          hidden
          (change)="import($event)"
        />
      </div>
      <div class="rules-bundle__row">
        <input
          pInputText
          type="text"
          placeholder="https://live.example.com"
          [ngModel]="liveUrl()"
          (ngModelChange)="onLiveUrlChange($event)"
          style="min-width: 260px"
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

  protected readonly exporting = signal(false);
  protected readonly importing = signal(false);
  protected readonly pulling = signal(false);
  protected readonly pushing = signal(false);
  protected readonly liveUrl = signal(RulesBundleControls.rememberedLiveUrl());
  protected readonly message = signal<string | null>(null);
  protected readonly conflictMessage = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);

  protected onLiveUrlChange(value: string): void {
    this.liveUrl.set(value);
    try {
      localStorage.setItem(LIVE_URL_STORAGE_KEY, value);
    } catch {
      // Private browsing or storage disabled -- the field still works for this session.
    }
  }

  private static rememberedLiveUrl(): string {
    try {
      return localStorage.getItem(LIVE_URL_STORAGE_KEY) ?? '';
    } catch {
      return '';
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
      .pullResolutionRules(url)
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
      .pushResolutionRules(url)
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

  protected export(): void {
    this.exporting.set(true);
    this.error.set(null);
    this.api
      .exportResolutionRules()
      .pipe(
        catchError((err: unknown) => {
          this.error.set(RulesBundleControls.describe(err, 'Could not export the rules.'));
          return of(null);
        }),
        takeUntilDestroyed(),
      )
      .subscribe((bundle) => {
        this.exporting.set(false);
        if (!bundle) {
          return;
        }
        const blob = new Blob([JSON.stringify(bundle, null, 2)], {
          type: 'application/json',
        });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `tecdoc-rules-${bundle.exported_at.slice(0, 10)}.json`;
        link.click();
        URL.revokeObjectURL(url);
        this.message.set(
          `Exported ${bundle.tecdoc_rules.length} TecDoc rule(s), ${bundle.ts_rules.length} TS rule(s).`,
        );
      });
  }

  protected import(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) {
      return;
    }
    this.importing.set(true);
    this.error.set(null);
    this.conflictMessage.set(null);
    file
      .text()
      .then((text) => {
        const bundle = JSON.parse(text) as RulesBundleExport;
        this.api
          .importResolutionRules(bundle)
          .pipe(
            catchError((err: unknown) => {
              this.error.set(RulesBundleControls.describe(err, 'Could not import the rules.'));
              return of(null);
            }),
            takeUntilDestroyed(),
          )
          .subscribe((result) => {
            this.importing.set(false);
            input.value = '';
            if (!result) {
              return;
            }
            this.reportImportResult(result, 'Imported');
          });
      })
      .catch(() => {
        this.importing.set(false);
        input.value = '';
        this.error.set('That file is not valid JSON.');
      });
  }

  private static describe(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } } | null)?.error?.detail;
    return typeof detail === 'string' && detail.length > 0 ? detail : fallback;
  }
}
