/**
 * Plain-language readings of the matcher's reason codes.
 *
 * Each entry follows the gate that emits the code (`fuzzy_matching.py`,
 * `confidence_routing.py`, `match_run_adapters.py`). A code without an entry
 * is shown as-is rather than guessed at.
 */

const FIELD_NAMES: Record<string, string> = {
  bodywork: 'body type',
  engine_code: 'engine code',
  power_kw: 'power',
  displacement_cc: 'displacement',
  drive_type: 'drive type',
  fuels: 'fuel',
  year: 'year',
  model: 'model',
};

export function fieldName(field: string): string {
  return FIELD_NAMES[field] ?? field.replace(/_/g, ' ');
}

const REASONS: Record<string, string> = {
  model_evidence_missing:
    'The car has no model the matcher can use, so it was never scored.',
  normalization_review_required:
    'Its registry data needs a normalization review before it can be matched.',
  no_candidate_above_threshold: 'No KType scored high enough to be a candidate.',
  context_conflict_requires_review:
    'The best KType conflicts with the car on a field, so it goes to review.',
  non_hard_context_conflict:
    'A conflict with the best KType keeps it in review, whatever its score.',
  candidate_margin_not_met:
    'Two or more KTypes score too close to pick one; a field that separates them would decide.',
  candidate_margin_below_gate:
    'The top KTypes are too close to separate safely.',
  phonetic_candidate_requires_review:
    'The model matched only by how it sounds (the spelling differs), which is review-only.',
  phonetic_evidence_requires_review:
    'The model matched only by how it sounds (the spelling differs), which is review-only.',
  partial_model_requires_review:
    'Only part of the model name matched (a family label), which is review-only.',
  manufacturer_scope_requires_review: 'The manufacturer did not match exactly.',
  manufacturer_scope_not_exact: 'The manufacturer did not match exactly.',
  automatic_threshold_not_met: "The best KType's score is below the automatic threshold.",
  provisional_threshold_met: 'Confidence reaches only the provisional level.',
  provisional_threshold_not_met: 'Confidence is below even the provisional level.',
  engine_code_unverified:
    "The car's engine code is unknown to the catalog, so it confirms nothing.",
  candidate_only_not_graph_safe:
    "The chosen KType is candidate-only (TecDoc can't say which engine it has) and the car's engine code does not confirm it.",
  candidate_only_engine_confirmed:
    "A candidate-only KType, confirmed by the car's own engine code.",
  model_inferred_by_rule: "The model came from a learned rule, not the car's own text.",
  automatic_candidate_threshold_met: 'The best KType cleared the automatic threshold.',
  resolved_threshold_met: 'Confidence meets the resolved threshold.',
};

/** What a reason code means, or null when there is no reviewed reading for it. */
export function describeReason(code: string): string | null {
  const bare = code.replace(/^(route|match):/, '');
  const [head, field] = bare.split(':', 2);
  if (head === 'context_conflict' && field) return `Conflicts with the best KType on ${fieldName(field)}.`;
  if (head === 'normalization' && field?.endsWith('_unrecognized')) {
    return `Normalization did not recognize the ${field.replace(/_unrecognized$/, '').replace(/_/g, ' ')}.`;
  }
  if (head === 'hard_conflict' && field) {
    return `Hard conflict on ${fieldName(field)}: this overrides any score.`;
  }
  return REASONS[bare] ?? null;
}
