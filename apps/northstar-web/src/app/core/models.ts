/** Response shapes mirrored from the FastAPI OpenAPI schema. */

export interface SourceRecord {
  id: number;
  source_batch_id: string;
  ingested_at: string;
  raw_record: Record<string, unknown>;
}

export interface SourceRecordPage {
  items: SourceRecord[];
  limit: number;
  next_cursor: number | null;
  has_more: boolean;
  total: number;
  total_is_estimate: boolean;
  timed_out: boolean;
  summary_fields: string[];
}

export interface SourceRecordNormalization {
  source_batch_id: string;
  status: string;
  confidence: number;
  normalized: Record<string, unknown>;
  candidates: Record<string, unknown>;
  applied_rule_ids: string[];
  review_reasons: string[];
  updated_at: string | null;
}

export interface SourceRecordDetail {
  record: SourceRecord;
  normalizations: SourceRecordNormalization[];
}

export interface SourceBatch {
  batch_id: string;
  records: number;
  status: string | null;
  finished_at: string | null;
}

export interface SourceFieldStat {
  field: string;
  present: number;
  non_null: number;
  fill_rate: number;
  examples: string[];
}

export interface SourceFieldInventory {
  sampled_rows: number;
  fields: SourceFieldStat[];
}

export interface CoverageBatch {
  batch_id: string;
  records: number;
  finished_at: string | null;
}

export interface FieldCoverage {
  field: string;
  normalized: number;
  candidates_only: number;
  missing: number;
  coverage: number;
}

export interface StatusBreakdown {
  resolved: number;
  provisional: number;
  review_required: number;
  failed: number;
}

export interface ReviewReasonCount {
  reason: string;
  records: number;
}

export interface TsCoverageReport {
  batch_id: string;
  rows: number;
  status: StatusBreakdown;
  fields: FieldCoverage[];
  review_reasons: ReviewReasonCount[];
  fully_normalized_fields: number;
  partial_fields: number;
}

export interface TecDocFieldCoverage {
  entity_type: string;
  field: string;
  present: number;
  missing: number;
  coverage: number;
}

export interface TecDocEntityCoverage {
  entity_type: string;
  rows: number;
  fields: TecDocFieldCoverage[];
}

export interface TecDocCoverageReport {
  batch_id: string | null;
  entities: TecDocEntityCoverage[];
}

export interface TecDocVehicle {
  ktype: string;
  alias_id: string;
  variant_id: string;
  source_name: string;
  manufacturer: string | null;
  model_family: string | null;
  engine_code: string | null;
  transmission_code: string | null;
  [key: string]: unknown;
}

export interface TecDocSummary {
  batch_id: string;
  source_version: string;
  source_rows: number;
  promoted_ktypes: number;
  engine_linked_ktypes: number;
  facts_only_ktypes: number;
  manufacturers: number;
  model_families: number;
  engines: number;
  hierarchy_status: string;
}

export interface TecDocPage {
  summary: TecDocSummary;
  filtered_total: number;
  limit: number;
  offset: number;
  items: TecDocVehicle[];
}

export interface TecDocEntity {
  [key: string]: unknown;
}

export interface TecDocEntityPage {
  kind: string;
  filtered_total: number;
  limit: number;
  offset: number;
  items: TecDocEntity[];
}

export interface RuleCatalogEntry {
  rule_id: string;
  area: string;
  source_fields: string[];
  source_terms: string[];
  canonical_field: string;
  base_canonical_value: string | null;
  effective_canonical_value: string | null;
  effective_decision: string;
  effective_display_value: string | null;
  vehicle_scopes: string[];
  manufacturers: string[];
  has_draft: boolean;
  change_note: string | null;
}

export interface RuleCatalogResponse {
  base_version: string;
  active_version: string;
  draft_count: number;
  total: number;
  filtered_total: number;
  limit: number;
  offset: number;
  areas: string[];
  canonical_fields: string[];
  canonical_options_by_field: Record<string, string[]>;
  items: RuleCatalogEntry[];
}

export interface MatchRunSummary {
  operation_id: string | null;
  status: string;
  processed: number;
  expected_source_rows: number;
  progress_percent: number;
  counts: Record<string, number>;
  blockers: Array<{
    code: string;
    title: string;
    guidance: string;
    count: number;
    pending: number;
    in_review: number;
    decided: number;
  }>;
}

export interface MatchReviewPattern {
  pattern_key: string;
  category: string;
  title: string;
  summary: string;
  source_values: Record<string, unknown>;
  candidate_values: Record<string, unknown>;
  why_blocked: string;
  decision_question: string;
  evidence_gaps: string[];
  sample_occurrences: number;
  category_occurrences: number;
  coverage: 'sample' | 'exhaustive';
  examples: Array<{ manufacturer: string; model: string; candidate_reference: string | null }>;
  decision: {
    action: 'accept_pattern' | 'keep_blocked' | 'change_rule';
    selected_values: string[];
    reviewer: string;
    reason: string;
    created_at: string;
  } | null;
}

export interface MatchReviewPatternPage {
  operation_id: string;
  category: string | null;
  patterns: MatchReviewPattern[];
}

// --- Unresolved fields: population-first resolution-rule authoring -------------------
// Mirrors api/app/features/match_review/chunk_schemas.py.

export interface MatchChunkBuild {
  build_id: string;
  source_batch_id: string;
  signature_version: string;
  status: string;
  row_count: number;
  chunk_count: number;
  started_at: string;
  finished_at: string | null;
}

export interface UnresolvedPopulation {
  source_field: string;
  source_value: string;
  signature_field: string;
  row_count: number;
}

export interface UnresolvedOverview {
  build_id: string;
  populations: UnresolvedPopulation[];
}

export interface FieldValueCount {
  value: string;
  count: number | null;
  /** What the register means by this code, when it defines one. */
  meaning: string | null;
}

export interface DiscriminatorField {
  field: string;
  distinct_count: number;
  present_count: number;
  coverage: number;
  separation: number;
  concision: number;
  score: number;
  usable: boolean;
  top_values: FieldValueCount[];
  /** True when the rule already tests this field; its counts then exclude its own clause. */
  constrained: boolean;
  selected_values: string[];
}

export interface DiscriminatorReport {
  build_id: string;
  source_field: string;
  source_value: string;
  signature_field: string;
  population: number;
  fields: DiscriminatorField[];
}

export type RuleOperator = 'equals' | 'not_equals' | 'starts_with' | 'contains' | 'gte' | 'lte';

/** One clause. Values are OR-ed within a clause; clauses are AND-ed together. */
export interface RuleCondition {
  field: string;
  value?: string | null;
  values?: string[] | null;
  layer: 'source' | 'normalized';
  operator: RuleOperator;
}

export interface NarrowingStep {
  label: string;
  matched_rows: number;
}

export interface RefineResult {
  matched_rows: number;
  would_resolve: number;
  already_resolved: number;
  signature_field: string;
  /** True when no identity-bearing field still varies, so one value is safe to assign. */
  homogeneous: boolean;
  varying_identity_fields: string[];
  trail: NarrowingStep[];
  fields: DiscriminatorField[];
}

export interface RulePreview {
  conditions: RuleCondition[];
  target_field: string;
  target_value: string;
  matched_rows: number;
  would_resolve: number;
  already_resolved: number;
  sample_plates: string[];
}

export interface ResolutionRule {
  rule_id: string;
  build_id: string;
  source_field: string;
  source_value: string;
  target_field: string;
  target_value: string;
  conditions: RuleCondition[];
  author: string;
  note: string | null;
  matched_rows: number;
  would_resolve: number;
  already_resolved: number;
  status: 'saved' | 'applied' | 'retired';
  resolved_rows: number;
  created_at: string;
  applied_at: string | null;
  applied_by: string | null;
  retired_at: string | null;
  retired_by: string | null;
  /** Rows this run wrote; null unless the call ran the rule. */
  resolved_now: number | null;
  /** Rows this call reopened; null unless the call retired it. */
  superseded_rows: number | null;
}

export interface RuleAdvice {
  advisor: string;
  confident: boolean;
  conditions: RuleCondition[];
  target_field: string;
  target_value: string | null;
  reasoning: string;
  evidence: Record<string, unknown>;
}

/** `closed` means the value must come from `values`; otherwise free text is accepted. */
export interface TargetVocabulary {
  target_field: string;
  closed: boolean;
  values: FieldValueCount[];
  source: 'reviewed_rules' | 'observed' | 'none';
}

export interface PopulationAttribute {
  field: string;
  distinct_count: number;
  present_count: number;
  top_values: FieldValueCount[];
}

export interface PopulationAttributes {
  build_id: string;
  source_field: string;
  source_value: string;
  population: number;
  scanned_members: number;
  /** True when counts come from a sample, not the full population. */
  sampled: boolean;
  attributes: PopulationAttribute[];
}

export interface ValuePatternSuggestion {
  prefix: string;
  row_count: number;
  distinct_values: number;
  coverage: number;
  score: number;
}

export interface PatternReport {
  field: string;
  population: number;
  patterns: ValuePatternSuggestion[];
}
