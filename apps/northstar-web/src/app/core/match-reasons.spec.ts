import { describe, expect, it } from 'vitest';

import { describeReason, fieldName } from './match-reasons';

describe('describeReason', () => {
  it('reads a code with or without its route/match prefix', () => {
    expect(describeReason('route:candidate_margin_below_gate')).toBe(
      'The top KTypes are too close to separate safely.',
    );
    expect(describeReason('model_evidence_missing')).toContain('no model');
  });

  it('names the field a conflict is on', () => {
    expect(describeReason('context_conflict:bodywork')).toBe(
      'Conflicts with the best KType on body type.',
    );
    expect(describeReason('hard_conflict:engine_code')).toContain('Hard conflict on engine code');
  });

  it('reads an unrecognized normalization input generically', () => {
    expect(describeReason('normalization:tyre_size_unrecognized')).toBe(
      'Normalization did not recognize the tyre size.',
    );
  });

  it('does not guess at a code it has no reading for', () => {
    expect(describeReason('policy:something_new')).toBeNull();
    expect(describeReason('normalization:tyre_size_missing')).toBeNull();
  });
});

describe('fieldName', () => {
  it('names known fields and spaces out the rest', () => {
    expect(fieldName('power_kw')).toBe('power');
    expect(fieldName('first_registration')).toBe('first registration');
  });
});
