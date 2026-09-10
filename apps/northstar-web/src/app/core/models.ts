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

// --- filtering the TecDoc vehicle population, ported from `/v1/vehicles` ----------------
// `conditions` reuses `RuleCondition`'s wire shape so `FilterState` can build a TecDoc
// filter the same way it builds a TS one; TecDoc has only one layer, so `layer` is sent
// as `'source'` and ignored server-side.

export interface TecDocVehicleFilter {
  query: string;
  conditions: RuleCondition[];
  unresolved_field?: string | null;
}

export interface TecDocVehicleCount {
  matched_rows: number;
  total_rows: number;
}

export interface TecDocUnresolvedField {
  field: string;
  label: string;
  unresolved: number;
  share: number;
}

export interface TecDocUnresolvedSummary {
  matched_rows: number;
  fields: TecDocUnresolvedField[];
}

export interface TecDocFacetValue {
  value: string;
  count: number;
}

export interface TecDocVehicleFacet {
  field: string;
  matched_rows: number;
  values: TecDocFacetValue[];
}

// --- the value-level gap: which raw codes/labels have no canonical target, and Resolve --

/** `energy_sources`/`bodywork_form`/`drive_type`/`transmission_type` are TecDoc's own
 * gap fields -- ruling on a raw code/label TecDoc's own data holds. `fuel`/`bodywork`/
 * `drive` are cross-system synonym fields -- declaring that a TS term and a TecDoc
 * term denote the same (or a broader-than) concept. Both write the same table through
 * the same `resolve` endpoint; see `TecDocResolveRequest`. */
export type TecDocResolvableField =
  | 'energy_sources'
  | 'bodywork_form'
  | 'drive_type'
  | 'transmission_type'
  | 'fuel'
  | 'bodywork'
  | 'drive';

/** `equivalent`: same real-world thing under two spellings, safe to treat as a match.
 * `compatible`: one side is coarser than the other (TS's undifferentiated `2wd` against
 * TecDoc's `fwd`/`rwd`), so it must score neutral, never as agreement -- and, unlike
 * `equivalent`, one source term can be compatible with more than one target. Only
 * meaningful for a synonym field; a TecDoc gap resolution is always `equivalent`. */
export type RuleRelation = 'equivalent' | 'compatible';

export interface TecDocResolution {
  decision: 'accepted' | 'excluded';
  canonical_value: string | null;
  note: string;
  reviewed_by: string;
  updated_at: string;
  source_system: RuleSource;
  relation: RuleRelation;
  support: number | null;
}

export interface TecDocGapValue {
  source_term: string;
  label: string | null;
  key_table: string | null;
  support: number;
  /** Set when this value must not be resolved with a single target (a mixed descriptor). */
  blocked_reason: string | null;
  resolution: TecDocResolution | null;
}

export interface TecDocGapValuesResponse {
  canonical_field: string;
  key_table: string | null;
  canonical_options: string[];
  values: TecDocGapValue[];
}

// --- one row's fields, each with its outcome -- opened from a KType, mirrors TS's record panel

export interface TecDocVehicleFieldStatus {
  canonical_field: string;
  source_term: string | null;
  label: string | null;
  canonical_value: string | null;
  status: 'resolved' | 'unresolved' | 'rule_resolved';
  blocked_reason: string | null;
}

export interface TecDocVehicleDetail {
  source_key: string;
  manufacturer: string | null;
  model_family: string | null;
  fields: TecDocVehicleFieldStatus[];
}

export interface TecDocResolveRequest {
  canonical_field: TecDocResolvableField;
  source_term: string;
  decision: 'accepted' | 'excluded';
  canonical_value?: string | null;
  note?: string;
  reviewed_by: string;
  /** Only meaningful on a synonym field; defaults to 'tecdoc' server-side. */
  source_system?: RuleSource;
  /** Only meaningful on a synonym field; defaults to 'equivalent' server-side. */
  relation?: RuleRelation;
  /** Required when `relation` is 'compatible'; ignored otherwise. */
  support?: number | null;
}

export type RuleOrigin = 'catalog' | 'code' | 'resolution' | 'reviewed_mapping' | 'generated';

/** Which dataset a rule normalizes. Both are normalized into the same canonical
 * vocabulary, so they share one catalog rather than two. */
export type RuleSource = 'transportstyrelsen' | 'tecdoc';

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
  origin: RuleOrigin;
  transformer_id: string | null;
  editable: boolean;
  notes: string | null;
  source: RuleSource;
  /** Rows carrying this value in the scanned release. TecDoc-generated rules only. */
  support: number | null;
  /** True for a value listed only so the catalogue is complete (a TecDoc model name
   * or manufacturer) -- no closed vocabulary exists to resolve it against. */
  inventory_only: boolean;
}

export interface TransformerStage {
  transformer_id: string;
  order: number;
  default_rule_id: string;
  summary: string;
  source_fields: string[];
  writes: string[];
  rule_areas: string[];
  code_areas: string[];
  catalog_rule_count: number;
  code_rule_count: number;
  review_reasons: string[];
}

export interface RuleCatalogResponse {
  base_version: string;
  active_version: string;
  draft_count: number;
  total: number;
  filtered_total: number;
  catalog_total: number;
  code_total: number;
  resolution_total: number;
  tecdoc_total: number;
  tecdoc_inventory_total: number;
  tecdoc_resolution_total: number;
  tecdoc_rule_version: string | null;
  pipeline_version: string;
  limit: number;
  offset: number;
  areas: string[];
  canonical_fields: string[];
  canonical_options_by_field: Record<string, string[]>;
  transformers: TransformerStage[];
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

// --- Filtering the whole vehicle population -----------------------------------------
// Mirrors api/app/features/vehicle_filter/schemas.py. The filter deliberately reuses
// RuleCondition: a filter that narrows a population and a rule that resolves one are the
// same expression at two moments, so they must not drift into two shapes.

export interface VehicleFilterRequest {
  conditions: RuleCondition[];
  /** Restrict to cars where this field is neither normalized nor filled by a live rule. */
  unresolved_field?: string | null;
}

export interface VehicleCount {
  matched_rows: number;
  total_rows: number;
}

export interface UnresolvedFieldCount {
  field: string;
  unresolved: number;
  share: number;
}

export interface UnresolvedSummary {
  matched_rows: number;
  fields: UnresolvedFieldCount[];
}

export interface VehicleFacet {
  field: string;
  matched_rows: number;
  values: FieldValueCount[];
}

export interface VehicleRow {
  source_record_id: number;
  plate: string | null;
  brand: string | null;
  model: string | null;
  variant: string | null;
  version: string | null;
  vehicle_year: number | null;
  kw: number | null;
  norm_status: string | null;
}

export interface VehiclePage {
  items: VehicleRow[];
  next_cursor: number | null;
  has_more: boolean;
}

export interface VehicleFieldStatus {
  field: string;
  source_field: string | null;
  source_value: string | null;
  normalized_value: string | null;
  resolved_value: string | null;
  status: 'resolved' | 'unresolved' | 'rule_resolved';
}

export interface VehicleDetail {
  source_record_id: number;
  plate: string | null;
  vin: string | null;
  norm_status: string | null;
  source_batch_id: string | null;
  fields: VehicleFieldStatus[];
}

/**
 * A run of one rule. Applying is a background job now: a rule covering the whole
 * population takes about a minute, so the request that starts it returns this and the
 * screen polls until it settles.
 */
export interface ResolutionRuleApplication {
  job_id: number;
  rule_id: string;
  status: 'running' | 'completed' | 'failed';
  rows_written: number;
  started_at: string;
  finished_at: string | null;
  error_summary: string | null;
}

/**
 * Populations of a gap collapsed by the shape of their value.
 *
 * Grouping by exact value is what the original worklist did, and it hid the largest
 * finding in the data: three Volvo spellings read as three unrelated populations among
 * 97,063, when one leading token accounts for 608,251 cars.
 */
export type GapGroupingMode = 'leading_token' | 'character_shape' | 'exact';

export interface GapGroup {
  label: string;
  rows: number;
  distinct_values: number;
  samples: string[];
}

export interface GapGroupReport {
  field: string;
  mode: GapGroupingMode;
  unresolved_field: string;
  total_rows: number;
  groups: GapGroup[];
}

