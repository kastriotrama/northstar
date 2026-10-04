import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { NorVehicleRecord, VehicleFieldValue } from '../core/models';
import {
  fieldGroups,
  fieldSourceLabel,
  originLabel,
  showFieldValue,
  showValue,
  sourceDetail,
  sourceLabel,
} from '../core/vehicle-record';

/** Groups a reader looks at first; the rest stay folded until asked for. */
const OPEN_GROUPS: readonly string[] = ['make', 'technical', 'dates'];

/**
 * One car's own information, read from its vehicle record: who it is, then every value
 * it carries in its groups, each with the source that supplied it and what lost to it.
 * Presentational -- the host loads the record. Read-only: values are corrected in the
 * matching panel beside it.
 */
@Component({
  selector: 'ns-vehicle-facts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dl class="ids">
      <dt>NOR ID</dt>
      <dd class="mono">{{ record().vehicle_id }}</dd>
      <dt>Status</dt>
      <dd>{{ record().registry_status === 'registered' ? 'In the register' : 'Deregistered' }}</dd>
      <dt>Created from</dt>
      <dd>
        {{ origin() }}
        @if (record().origin_observed_on; as observed) {
          <span class="muted">· {{ observed }}</span>
        }
      </dd>
      @for (identifier of identifiers(); track identifier.kind + identifier.value) {
        <dt>{{ identifier.kind === 'vin' ? 'VIN' : 'Chassis no.' }}</dt>
        <dd class="mono">{{ identifier.value }}</dd>
      }
    </dl>

    @for (group of groups(); track group.group) {
      <details class="group" [open]="isOpen(group.group)">
        <summary>
          {{ group.label }}
          <span class="muted">
            {{ group.filled.length }} set
            @if (group.empty) {
              · {{ group.empty }} unknown
            }
          </span>
        </summary>
        <dl class="fields">
          @for (field of group.filled; track field.field) {
            <div class="field">
              <dt>{{ field.label }}</dt>
              <dd>
                <span>{{ show(field) }}</span>
                @if (badge(field); as text) {
                  <span class="source source--{{ field.source?.source }}" [title]="detail(field)">{{ text }}</span>
                }
                @for (alternative of field.alternatives; track alternative.source.source) {
                  <span class="alternative">
                    {{ sourceLabel(alternative.source) }} said <s>{{ showValue(alternative.value) }}</s>
                  </span>
                }
              </dd>
            </div>
          } @empty {
            <p class="muted none">Nothing recorded.</p>
          }
        </dl>
      </details>
    }
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 0.35rem; font-size: 0.78rem; }
    dl, dd, p { margin: 0; }
    .ids { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 0.15rem 0.7rem; }
    .ids dt, .field dt { color: var(--p-text-muted-color); }
    .ids dd { overflow-wrap: anywhere; }
    .group { border-top: 1px solid var(--p-surface-100); padding-top: 0.3rem; }
    summary { cursor: pointer; font-weight: 600; }
    summary .muted { font-weight: 400; }
    .fields {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr));
      gap: 0.3rem 0.9rem; padding: 0.3rem 0 0.1rem;
    }
    .field { min-width: 0; }
    .field dt { font-size: 0.7rem; }
    .field dd { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.15rem 0.35rem; overflow-wrap: anywhere; }
    .source {
      font-size: 0.64rem; padding: 0.02rem 0.35rem; border-radius: 999px;
      background: var(--p-surface-100); color: var(--p-text-muted-color); white-space: nowrap;
    }
    .source--ais { background: #e4eefc; color: #1d4f91; }
    .source--review, .source--correction { background: #efe6fb; color: #5b2d91; }
    .source--rule { background: #fff3d6; color: #7a5300; }
    .alternative { flex-basis: 100%; font-size: 0.68rem; color: var(--p-text-muted-color); }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  `,
})
export class VehicleFacts {
  readonly record = input.required<NorVehicleRecord>();

  protected readonly groups = computed(() => fieldGroups(this.record()));
  protected readonly origin = computed(() => originLabel(this.record().origin_source));
  /** The VIN or chassis number the car carries today; plates are in the list beside it. */
  protected readonly identifiers = computed(() =>
    this.record().identifiers.filter((identifier) => identifier.kind !== 'plate' && identifier.current),
  );

  protected readonly show = showFieldValue;
  protected readonly showValue = showValue;
  protected readonly sourceLabel = sourceLabel;

  protected isOpen(group: string): boolean {
    return OPEN_GROUPS.includes(group);
  }

  /** The source that won, named only when it is not the record that created the car. */
  protected badge(field: VehicleFieldValue): string {
    return field.source && !field.source.origin ? fieldSourceLabel(field) : '';
  }

  protected detail(field: VehicleFieldValue): string {
    return field.source ? sourceDetail(field.source) : '';
  }
}
