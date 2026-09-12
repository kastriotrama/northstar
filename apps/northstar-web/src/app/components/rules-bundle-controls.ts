import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, of } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';

import { Api } from '../core/api';
import type { RulesBundleExport } from '../core/models';

/**
 * Export/import every manually-authored rule (TecDoc gap rulings + TS resolution
 * rules) as one JSON file.
 *
 * The DB is always the source of truth for what's live; this is a carrier between
 * databases -- a rule added on one machine reaches another with a click instead of
 * running `scripts/export_resolution_rules.py` / `load_resolution_rules.py` by hand.
 * Shared between `/tecdoc` (where a rule is usually first written) and `/rules`
 * (which already browses both sources together) so either screen can move rules
 * without duplicating the download/upload/parsing logic.
 */
@Component({
  selector: 'ns-rules-bundle-controls',
  imports: [ButtonModule],
  template: `
    <div class="rules-bundle">
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
      @if (message(); as text) {
        <span class="rules-bundle__hint">{{ text }}</span>
      }
      @if (error(); as text) {
        <span class="rules-bundle__hint rules-bundle__hint--error">{{ text }}</span>
      }
    </div>
  `,
  styles: `
    .rules-bundle {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
    }
    .rules-bundle__hint {
      font-size: 0.78rem;
      color: var(--p-text-muted-color);
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
  protected readonly message = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);

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
            this.message.set(
              `Imported: ${result.tecdoc_single_target + result.tecdoc_compatible} TecDoc rule(s), ` +
                `${result.ts_created} new TS rule(s) (${result.ts_already_present} already present, ` +
                `${result.ts_skipped_invalid} skipped).`,
            );
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
