import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { catchError, map, of, startWith, switchMap } from 'rxjs';

import { Api } from '../core/api';
import type { KTypeCandidate, MatchBucket, VehicleMatchLookup } from '../core/models';

interface Chip {
  field: string;
  text: string;
  state: 'conflict' | 'missing' | 'ok';
}

const BUCKET_LABELS: Record<MatchBucket, string> = {
  one: 'One KType',
  several: 'Several KTypes',
  none: 'No KType',
  not_matchable: 'Not matchable',
};

/**
 * Which TecDoc KTypes one car could be, read from the real matcher.
 *
 * Shown inside the Vehicles record panel for the exact record open there. Every
 * candidate the matcher weighed is listed with its catalog values; a value that
 * conflicts with the car is marked, so why a KType was ruled out reads off the chip.
 */
@Component({
  selector: 'ns-ktype-candidates',
  imports: [DecimalPipe],
  template: `
    @if (state(); as current) {
      @if (current.loading) {
        <p class="muted">Matching against TecDoc…</p>
      } @else if (current.error) {
        <p class="error">{{ current.error }}</p>
      } @else if (current.lookup; as result) {
        <div class="head">
          <span class="bucket bucket--{{ result.bucket }}">{{ bucketLabel(result.bucket) }}</span>
          <span class="muted">
            pipeline: <b>{{ result.terminal }}</b>
            @if (result.confidence !== null) {
              · {{ result.confidence | number: '1.0-2' }}
            }
          </span>
        </div>

        @if (gap(); as text) {
          <p class="gap">{{ text }}</p>
        }

        @if (result.inputs; as seen) {
          <details class="block">
            <summary>What the matcher saw</summary>
            <dl class="kv">
              <dt>manufacturer</dt>
              <dd>{{ seen.manufacturer }}</dd>
              <dt>model</dt>
              <dd>
                {{ seen.model_values.join(' / ') }}
                @if (seen.model_recovered_from) {
                  <span class="muted">({{ seen.model_recovered_from }})</span>
                }
              </dd>
              <dt>year</dt>
              <dd [class.gone]="seen.production_year === null">{{ seen.production_year ?? '—' }}</dd>
              <dt>fuel</dt>
              <dd [class.gone]="!seen.fuels.length">{{ seen.fuels.join(', ') || '—' }}</dd>
              <dt>engine code</dt>
              <dd [class.gone]="!seen.engine_code" [class.ruled]="filled('engine_code')">
                {{ seen.engine_code ?? '—' }}
              </dd>
              <dt>power</dt>
              <dd [class.gone]="seen.power_kw === null" [class.ruled]="filled('power_kw')">
                {{ seen.power_kw ?? '—' }} kW
              </dd>
              <dt>displacement</dt>
              <dd [class.gone]="seen.displacement_cc === null" [class.ruled]="filled('displacement_cc')">
                {{ seen.displacement_cc ?? '—' }} cc
              </dd>
              <dt>drive</dt>
              <dd [class.gone]="!seen.drive_type" [class.ruled]="filled('drive_type')">
                {{ seen.drive_type ?? '—' }}
              </dd>
              <dt>bodywork</dt>
              <dd [class.gone]="!seen.bodywork_form" [class.ruled]="filled('bodywork_form')">
                {{ seen.bodywork_form ?? '—' }}
              </dd>
            </dl>
            @if (result.rule_filled.length) {
              <p class="muted note">Underlined: supplied by a live rule.</p>
            }
          </details>
        }

        @for (candidate of result.candidates; track candidate.ktype) {
          <div class="candidate" [class.candidate--out]="!candidate.compatible">
            <div class="candidate__head">
              <span class="mono">{{ candidate.ktype }}</span>
              <span class="candidate__model">{{ candidate.model }}</span>
              <span
                [class.muted]="!candidate.conflicting_fields.includes('year')"
                [class.chip--conflict]="candidate.conflicting_fields.includes('year')"
                >{{ years(candidate) }}</span
              >
              @if (candidate.ktype === result.top_ktype) {
                <span class="tag">top</span>
              }
              @if (candidate.candidate_only) {
                <span class="tag tag--warn" title="Candidate-only KType: never auto-resolved">candidate-only</span>
              }
            </div>
            <div class="chips">
              @for (chip of chips(candidate); track chip.field) {
                <span class="chip chip--{{ chip.state }}" [title]="chip.field">{{ chip.text }}</span>
              }
            </div>
            @if (candidate.conflicting_fields.length) {
              <p class="candidate__why">Ruled out: {{ candidate.conflicting_fields.join(', ') }}</p>
            }
          </div>
        } @empty {
          @if (result.bucket === 'not_matchable') {
            <p class="muted">Stopped before matching: {{ result.reason_codes.join(', ') }}</p>
          } @else {
            <p class="muted">No KType cleared the matcher's threshold.</p>
          }
        }

        @if (result.candidates.length >= result.candidate_limit) {
          <p class="muted note">
            The matcher returns at most {{ result.candidate_limit }} candidates; there may be more.
          </p>
        }

        <details class="block">
          <summary>Reason codes</summary>
          <ul class="reasons">
            @for (reason of result.reason_codes; track reason) {
              <li class="mono">{{ reason }}</li>
            }
          </ul>
          <p class="muted note">Catalog: <span class="mono">{{ result.catalog_batch }}</span></p>
        </details>
      }
    }
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      font-size: 0.8rem;
    }
    .head {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
    }
    .bucket {
      font-weight: 650;
      padding: 0.1rem 0.5rem;
      border-radius: 999px;
    }
    .bucket--one { background: #dff3e4; color: #1c5a2e; }
    .bucket--several { background: #fff3d6; color: #7a5300; }
    .bucket--none { background: #fde4e4; color: #8a2020; }
    .bucket--not_matchable { background: var(--p-surface-100); color: var(--p-text-muted-color); }
    .gap {
      margin: 0;
      padding: 0.4rem 0.6rem;
      border-left: 3px solid #d99a00;
      background: #fffaf0;
    }
    .block summary {
      cursor: pointer;
      font-weight: 600;
    }
    .kv {
      display: grid;
      grid-template-columns: minmax(90px, 35%) 1fr;
      gap: 0.15rem 0.6rem;
      margin: 0.4rem 0 0;
    }
    .kv dt { color: var(--p-text-muted-color); }
    .kv dd { margin: 0; }
    .gone { color: var(--p-text-muted-color); opacity: 0.6; }
    .ruled { text-decoration: underline dotted; text-underline-offset: 3px; }
    .candidate {
      border: 1px solid var(--p-surface-200);
      border-radius: 6px;
      padding: 0.4rem 0.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .candidate--out { opacity: 0.7; background: var(--p-surface-50); }
    .candidate__head {
      display: flex;
      align-items: baseline;
      gap: 0.4rem;
      flex-wrap: wrap;
    }
    .candidate__model { font-weight: 600; }
    .candidate__why { margin: 0; color: #8a2020; }
    .chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
    .chip {
      padding: 0.05rem 0.4rem;
      border-radius: 4px;
      background: var(--p-surface-100);
    }
    .chip--conflict { background: #fde4e4; color: #8a2020; font-weight: 600; }
    .chip--missing { background: transparent; border: 1px dashed var(--p-surface-300); }
    .tag {
      font-size: 0.66rem;
      padding: 0.05rem 0.35rem;
      border-radius: 999px;
      background: #dde8f7;
      color: #1f4d85;
    }
    .tag--warn { background: #fff3d6; color: #7a5300; }
    .reasons { margin: 0.3rem 0 0; padding-left: 1rem; }
    .note { margin: 0.2rem 0 0; font-size: 0.72rem; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { margin: 0; color: #8a2020; }
  `,
})
export class KTypeCandidates {
  private readonly api = inject(Api);

  /** The exact record open in the panel; a plate can carry several. */
  readonly sourceRecordId = input.required<number>();

  protected readonly state = signal<{
    loading: boolean;
    error: string | null;
    lookup: VehicleMatchLookup | null;
  } | null>(null);

  constructor() {
    toObservable(this.sourceRecordId)
      .pipe(
        switchMap((id) =>
          this.api.matchLookup(id).pipe(
            map((lookup) => ({ loading: false, error: null, lookup })),
            catchError((err: { status?: number; error?: { detail?: string } }) =>
              of({
                loading: false,
                error: err?.error?.detail ?? 'Matching is unavailable right now.',
                lookup: null,
              }),
            ),
            // The first lookup builds the matcher (seconds); say so rather than sit blank.
            startWith({ loading: true, error: null, lookup: null }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((next) => this.state.set(next));
  }

  /** The one sentence that says where this car's gap is. */
  protected readonly gap = computed(() => {
    const result = this.state()?.lookup;
    if (!result) return null;
    const compatible = result.candidates.filter((candidate) => candidate.compatible).length;
    if (result.bucket === 'several') {
      if (result.missing_separating_fields.length) {
        return `${compatible} KTypes fit. They differ on ${result.separating_fields.join(', ')} — and this car has no ${result.missing_separating_fields.join(' or ')}.`;
      }
      return `${compatible} KTypes fit, differing on ${result.separating_fields.join(', ') || 'nothing the matcher compares'}.`;
    }
    if (result.bucket === 'none' && result.candidates.length) {
      const fields = [...new Set(result.candidates.flatMap((c) => c.conflicting_fields))];
      return `Every candidate conflicts with this car, on ${fields.join(', ')}.`;
    }
    return null;
  });

  protected bucketLabel(bucket: MatchBucket): string {
    return BUCKET_LABELS[bucket];
  }

  protected filled(field: string): boolean {
    return this.state()?.lookup?.rule_filled.includes(field) ?? false;
  }

  protected years(candidate: KTypeCandidate): string {
    if (candidate.year_from === null && candidate.year_to === null) return '';
    return `${candidate.year_from ?? '…'}–${candidate.year_to ?? 'now'}`;
  }

  /** Each catalog value as a chip, marked by how it compares with the car. */
  protected chips(candidate: KTypeCandidate): Chip[] {
    const state = (field: string): Chip['state'] =>
      candidate.conflicting_fields.includes(field)
        ? 'conflict'
        : candidate.missing_fields.includes(field)
          ? 'missing'
          : 'ok';
    const chips: Chip[] = [
      { field: 'year', text: this.years(candidate) || 'no years', state: state('year') },
      {
        field: 'power_kw',
        text: candidate.power_kw !== null ? `${candidate.power_kw} kW` : 'kW ?',
        state: state('power_kw'),
      },
      {
        field: 'displacement_cc',
        text: candidate.displacement_cc !== null ? `${candidate.displacement_cc} cc` : 'cc ?',
        state: state('displacement_cc'),
      },
      {
        field: 'engine_code',
        text: candidate.engine_codes.join(' / ') || 'engine ?',
        state: state('engine_code'),
      },
      { field: 'fuels', text: candidate.fuels.join(', ') || 'fuel ?', state: state('fuels') },
      { field: 'drive_type', text: candidate.drive_type ?? 'drive ?', state: state('drive_type') },
      {
        field: 'bodywork',
        text: candidate.bodyworks.join(', ') || 'body ?',
        state: state('bodywork'),
      },
    ];
    return chips.filter((chip) => chip.field !== 'year');
  }
}
