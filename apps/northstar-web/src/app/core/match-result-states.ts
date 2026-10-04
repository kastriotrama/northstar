import type { MatchResultState } from './models';

export interface MatchResultStateInfo {
  key: MatchResultState;
  label: string;
  hint: string;
}

/** The overview's states, in the order a reviewer reads them. */
export const MATCH_RESULT_STATES: ReadonlyArray<MatchResultStateInfo> = [
  { key: 'resolved', label: 'Resolved', hint: 'the matcher accepted one KType' },
  { key: 'several', label: 'Several KTypes', hint: 'two or more fit and none was accepted' },
  {
    key: 'one_unconfirmed',
    label: 'One, not confirmed',
    hint: 'exactly one KType fits but was not accepted automatically',
  },
  { key: 'none', label: 'No KType', hint: 'every candidate conflicts, or none was found' },
  { key: 'not_matchable', label: 'Not matchable', hint: 'stopped before matching' },
  { key: 'chosen', label: 'Chosen by a person', hint: 'a person chose the KType' },
  { key: 'chosen_none', label: 'None of these', hint: 'a person said no candidate is right' },
  { key: 'not_evaluated', label: 'Not evaluated yet', hint: 'no stored result for the car' },
];

export function matchResultStateLabel(state: string): string {
  return MATCH_RESULT_STATES.find((item) => item.key === state)?.label ?? state.replace(/_/g, ' ');
}
