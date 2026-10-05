import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, catchError, map, of, startWith, switchMap } from 'rxjs';

import { Api } from '../core/api';
import { fieldName } from '../core/match-reasons';
import type { ReviewerRuleChange } from '../core/models';

interface State {
  loading: boolean;
  error: string | null;
  rules: ReviewerRuleChange[];
}

/**
 * The reviewer rules made on the TS data screen, listed as what they are for the
 * Vehicles tab: changes to many cars. Who made each, what it sets, how many
 * vehicles it reached, and whether those vehicles' match results are still older
 * than the change. Read-only: a rule is run and retired where it was made.
 */
@Component({
  selector: 'ns-reviewer-rules',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, DecimalPipe],
  template: `
    <div class="head">
      <h3>Reviewer rules</h3>
      <button type="button" [disabled]="loading()" (click)="reload.next()">Refresh</button>
    </div>
    <p class="muted">
      Rules made on the TS data screen. Each sets a value on every car its conditions cover.
      They are run and retired there; this list shows what they changed. The latest
      {{ limit }} are listed.
    </p>
    @if (error(); as text) {
      <p class="error" role="alert">{{ text }}</p>
    }
    @if (rules().length) {
      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>By</th>
              <th>If</th>
              <th>Sets</th>
              <th class="num">Cars</th>
              <th class="num">Match result out of date</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            @for (rule of rules(); track rule.rule_id) {
              <tr [class.retired]="rule.status === 'retired'">
                <td class="nowrap">{{ when(rule) | date: 'yyyy-MM-dd HH:mm' }}</td>
                <td>{{ rule.applied_by ?? rule.author }}</td>
                <td class="conditions">{{ rule.conditions }}</td>
                <td>
                  {{ name(rule.target_field) }} = <b>{{ rule.target_value }}</b>
                  @if (rule.override) {
                    <span class="tag" title="Replaces a value the car already had">replaces</span>
                  }
                </td>
                <td class="num">{{ rule.vehicles | number }}</td>
                <td class="num" [class.stale]="rule.out_of_date > 0">
                  {{ rule.out_of_date | number }}
                </td>
                <td>
                  {{ rule.status }}
                  @if (rule.status === 'retired' && rule.retired_by) {
                    <span class="muted">by {{ rule.retired_by }}</span>
                  }
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    } @else if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (!error()) {
      <p class="muted">No reviewer rules yet.</p>
    }
  `,
  styles: `
    :host { display: block; margin-top: 1.2rem; font-size: 0.82rem; }
    .head { display: flex; align-items: center; gap: 0.8rem; }
    h3 { margin: 0; font-size: 0.95rem; }
    .scroll { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; font-weight: 600; color: var(--p-text-muted-color); font-size: 0.74rem; }
    td, th { padding: 0.25rem 0.4rem; border-bottom: 1px solid var(--p-surface-100); vertical-align: top; }
    .num { text-align: right; }
    .nowrap { white-space: nowrap; }
    .conditions { max-width: 28rem; overflow-wrap: anywhere; }
    .stale { color: #7a5300; font-weight: 700; }
    .retired td { color: var(--p-text-muted-color); }
    .tag { font-size: 0.66rem; padding: 0.05rem 0.35rem; border-radius: 999px; background: var(--p-surface-100); }
    .muted { color: var(--p-text-muted-color); }
    .error { color: #8a2020; }
  `,
})
export class ReviewerRules {
  private readonly api = inject(Api);

  protected readonly limit = 50;
  protected readonly reload = new Subject<void>();
  private readonly state = signal<State>({ loading: true, error: null, rules: [] });
  protected readonly rules = computed(() => this.state().rules);
  protected readonly loading = computed(() => this.state().loading);
  protected readonly error = computed(() => this.state().error);

  constructor() {
    this.reload
      .pipe(
        startWith(undefined),
        switchMap(() =>
          this.api.reviewerRuleChanges(this.limit).pipe(
            map(({ rules }): State => ({ loading: false, error: null, rules })),
            catchError(() =>
              of<State>({
                loading: false,
                error: 'Could not load the reviewer rules.',
                rules: this.state().rules,
              }),
            ),
            startWith<State>({ loading: true, error: null, rules: this.state().rules }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((next) => this.state.set(next));
  }

  /** When the rule last changed cars: retired, else run, else made. */
  protected when(rule: ReviewerRuleChange): string {
    return rule.retired_at ?? rule.applied_at ?? rule.created_at;
  }

  protected name(field: string): string {
    return fieldName(field);
  }
}
