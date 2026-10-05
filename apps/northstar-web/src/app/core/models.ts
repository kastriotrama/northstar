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

// --- filtering the TecDoc vehicle population, ported from `/v1/ts-records` ------------
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

export type RuleOrigin =
  | 'catalog'
  | 'code'
  | 'resolution'
  | 'reviewed_mapping'
  | 'generated'
  | 'policy';

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
  policy_total: number;
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
  /** Matched cars already carrying a different value — what a correction rewrites. */
  would_overwrite: number;
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
  /** Whether this rule rewrites cars that already carry a different value. */
  override: boolean;
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
 * A run of a full TecDoc reimport -- fresh `.dat` extraction written to
 * Postgres and the live graph. Also a background job: the full drop takes
 * several minutes, so the screen polls this until it settles.
 */
export interface TecDocReimportStatus {
  job_id: number;
  batch_id: string;
  status: 'running' | 'completed' | 'failed';
  source_ktypes: number;
  graph_rows_written: number;
  started_at: string;
  finished_at: string | null;
  error_summary: string | null;
}

/** Every manually-authored rule (TecDoc + TS) in this database, as one portable
 * file -- downloaded by the Export button, handed back to Import on another. */
export interface RulesBundleExport {
  exported_at: string;
  tecdoc_rules: Record<string, unknown>[];
  ts_rules: Record<string, unknown>[];
}

export interface RulesBundleImportResult {
  tecdoc_single_target: number;
  tecdoc_compatible: number;
  ts_created: number;
  ts_already_present: number;
  ts_skipped_invalid: number;
  ts_target_build: string | null;
  ts_rules_queued_for_apply: string[];
  /** TecDoc rows this database's own copy was newer than -- left untouched.
   * Non-empty means a real edit collision, not a plain one-way copy. */
  tecdoc_conflicts: Record<string, unknown>[];
  /** TS rules the pull carried that were left out because this database has
   * no completed build to attach them to yet. The TecDoc half still landed --
   * run a build here, then pull again, to pick these up. */
  ts_skipped_no_build: number;
  /** New `translation_rule_versions` rows (the `policy_total` overlay) added
   * by this import. A version already present here is left untouched --
   * the table is append-only, so there's no edit-collision case. */
  policy_versions_created: number;
  policy_versions_already_present: number;
  /** New sealed `core.tecdoc_rule_versions` (the generated `tecdoc_total`
   * catalog) added by this import, plus how many already existed here. */
  tecdoc_rule_versions_created: number;
  tecdoc_rule_versions_already_present: number;
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


// --- NorthStar vehicles (`/v1/vehicles`, `core.vehicles`) ------------------------------
// One physical car, keyed by its `NOR-` ID, carrying the value that won each field across
// every provider. Plates and VINs are identifiers of it with a history, never its key.

export type VehicleOperator = 'equals' | 'not_equals' | 'starts_with' | 'contains' | 'gte' | 'lte';

/** One clause on one vehicle column: values OR-ed, clauses AND-ed. No layer to choose. */
export interface VehicleCondition {
  field: string;
  values: string[];
  operator: VehicleOperator;
}

export interface VehicleSearchRequest {
  conditions: VehicleCondition[];
  /** Plate, VIN, NOR ID, a previous plate, or make/model words. */
  text: string;
}

export interface VehicleFieldInfo {
  field: string;
  label: string;
  group: string;
  sql_type: string;
  filterable: boolean;
  reviewable: boolean;
}

export interface NorVehicleRow {
  vehicle_id: string;
  plate: string | null;
  vin: string | null;
  /** `registered` or `deregistered`. */
  registry_status: string;
  vehicle_scope: string | null;
  manufacturer: string | null;
  model_family: string | null;
  production_year: number | null;
  power_kw: number | null;
  displacement_cc: number | null;
  engine_code: string | null;
  fuel: string | null;
  transmission: string | null;
  drive_type: string | null;
  bodywork_form: string | null;
  colour: string | null;
  ktype: string | null;
  /** `manual` when a person chose the KType, `manual_none` for "none of these". */
  match_state?: string | null;
  /** The matcher's stored state for the car; null when it has no stored result. */
  match_result?: string | null;
  /** The KType the matcher accepted, from the stored result. */
  automatic_ktype?: string | null;
  /** The possible KTypes, best first, with the matcher's confidence in each (stored result). */
  candidate_ktypes?: string[];
  candidate_confidences?: number[];
  /** Fields whose value a reviewer's rule asserted. */
  review_fields: string[];
  /** Fields a learned enrichment rule filled because no source stated them. */
  rule_fields: string[];
}

export interface NorVehiclePage {
  items: NorVehicleRow[];
  /** Sent on the first page only. */
  matched_rows: number | null;
  /** Keyset cursor: the last row's NOR ID. */
  next_cursor: string | null;
  has_more: boolean;
}

export interface NorVehicleFacet {
  field: string;
  values: { value: string; count: number }[];
}

/** Where one value came from. `origin` marks the record that created the vehicle. */
export interface ValueSource {
  source: string;
  ref: string | null;
  observed_on: string | null;
  origin: boolean;
}

export interface VehicleFieldValue {
  field: string;
  label: string;
  group: string;
  value: unknown;
  source: ValueSource | null;
  /** Values a source stated that lost; kept so a retired rule can fall back. */
  alternatives: { value: unknown; source: ValueSource }[];
}

export interface VehicleIdentifier {
  kind: 'vin' | 'chassis' | 'plate';
  value: string;
  valid_from: string | null;
  valid_to: string | null;
  current: boolean;
  source: string;
  source_ref: string | null;
}

export interface VehicleSourceLink {
  source_system: string;
  source_record_key: string;
  observed_on: string | null;
  link_method: string;
  linked_at: string;
}

export interface EnrichmentRuleInfo {
  rule_id: string;
  rule_family: string;
  target_field: string;
  key_fields: string[];
  key_values: string[];
  value: string;
  support: number;
  agreement: number;
  status: string;
}

/** One vehicle in full: each value with its source, identifiers over time, links, rules. */
export interface NorVehicleRecord {
  vehicle_id: string;
  origin_source: string;
  origin_observed_on: string | null;
  ts_record_id: number | null;
  registry_status: string;
  created_at: string;
  updated_at: string;
  fields: VehicleFieldValue[];
  identifiers: VehicleIdentifier[];
  source_links: VehicleSourceLink[];
  source_link_count: number;
  rules: EnrichmentRuleInfo[];
}

// --- TS-to-TecDoc matching diagnostics (`/v1/vehicles/matching`) ----------------------

/** `one`/`several`/`none` count KTypes that conflict with the car on no field. */
export type MatchBucket = 'one' | 'several' | 'none' | 'not_matchable';

/** What the matcher keyed on, after rule-filled values were applied. */
export interface MatcherInputs {
  manufacturer: string;
  model_values: string[];
  production_year: number | null;
  fuels: string[];
  engine_code: string | null;
  displacement_cc: number | null;
  power_kw: number | null;
  drive_type: string | null;
  bodywork_form: string | null;
  model_recovered_from: string | null;
  build_month?: number | null;
  electrification?: string | null;
}

export interface KTypeCandidate {
  ktype: string;
  candidate_only: boolean;
  confidence: number;
  manufacturer: string;
  model: string;
  year_from: number | null;
  year_to: number | null;
  fuels: string[];
  engine_codes: string[];
  displacement_cc: number | null;
  power_kw: number | null;
  drive_type: string | null;
  bodyworks: string[];
  matched_fields: string[];
  missing_fields: string[];
  conflicting_fields: string[];
  compatible: boolean;
}

export interface VehicleMatchLookup {
  /** The NorthStar vehicle matched; null when one TS record was asked for. */
  vehicle_id: string | null;
  /** The TS record whose derivation the vehicle's values were laid over. */
  source_record_id: number | null;
  plate: string | null;
  vin: string | null;
  catalog_batch: string;
  terminal: string;
  bucket: MatchBucket;
  confidence: number | null;
  top_ktype: string | null;
  reason_codes: string[];
  /** Why the pipeline ended where it did: the routing gate's explanation; null if stopped before matching. */
  verdict: string | null;
  rule_filled: string[];
  /** Vehicle values that replaced or filled the TS derivation, with their source. */
  overlaid_fields: Record<string, string>;
  inputs: MatcherInputs | null;
  candidates: KTypeCandidate[];
  /** The matcher returns at most this many; a full list means "this many or more". */
  candidate_limit: number;
  separating_fields: string[];
  /** Separating fields the car has no value for: the gap to close. */
  missing_separating_fields: string[];
  decision_trace: Record<string, unknown>[];
  /** Other vehicles that held this plate or VIN before, most recent first. */
  other_vehicle_ids: string[];
  /** The active translation rule set the matcher was built from. */
  rule_set_version?: string | null;
  /** Identifies the evidence shown; sent back with a choice so the server stores what was seen. */
  evidence_fingerprint?: string;
  /** A person's stored choice for this car; null without one or for a TS-record lookup. */
  choice?: KTypeChoiceState | null;
  /** The car's KType: the person's choice, else the matcher's when it resolved. */
  effective_ktype?: string | null;
  effective_source?: 'person' | 'matcher' | null;
  /** The head of each correction chain this car has, withdrawn ones too; by field. */
  corrections?: FactCorrectionState[];
  /** The fields a person may correct for this car; empty for a TS-record lookup. */
  correctable_fields?: CorrectableField[];
  /**
   * Why this car's record was stopped before matching (`tyre_size_unrecognized`, ...); empty for
   * a car that was never stopped. A person may release the car: a correction of the field
   * `normalization_stop`, which is in `corrections` and not in `correctable_fields`.
   */
  stop_reasons?: string[];
  /** Fields whose stored vehicle copy no longer agrees with what the matcher is handed. */
  copy_drift?: string[];
}

// --- A person's KType choice per car (`/v1/vehicles/{id}/ktype-choices`) --------------

export type KTypeChoiceAction = 'choose' | 'none' | 'withdraw';

export type KTypeChoiceStaleReason =
  | 'catalog_batch_changed'
  | 'evidence_changed'
  | 'ktype_not_in_catalog'
  | 'ktype_not_a_candidate'
  | 'new_candidates';

export interface KTypeChoiceChangedInput {
  field: string;
  then: unknown;
  now: unknown;
}

/** The head of a car's choice chain, with whether it still fits today's matching. */
export interface KTypeChoiceState {
  status: 'chosen' | 'none' | 'withdrawn';
  choice_id: string;
  ktype: string | null;
  reviewer: string;
  reason: string | null;
  created_at: string;
  catalog_batch: string;
  automatic_terminal: string;
  automatic_ktype: string | null;
  /** The chosen KType's entry exactly as it was stored with the choice. */
  chosen_candidate: KTypeCandidate | null;
  needs_review: boolean;
  stale_reasons: KTypeChoiceStaleReason[];
  changed_inputs: KTypeChoiceChangedInput[];
  history_count: number;
}

export interface KTypeChoiceRequest {
  /** Minted once per action and resent unchanged on a retry; becomes the choice id. */
  operation_id: string;
  action: KTypeChoiceAction;
  ktype?: string | null;
  reviewer: string;
  reason?: string | null;
  /** The choice the screen showed (also a withdrawn one), or null without one. */
  supersedes_choice_id: string | null;
  evidence_fingerprint?: string | null;
}

export interface KTypeChoiceHistoryEntry {
  choice_id: string;
  action: KTypeChoiceAction;
  ktype: string | null;
  reviewer: string;
  reason: string | null;
  created_at: string;
  supersedes_choice_id: string | null;
  catalog_batch: string;
  automatic_terminal: string;
  automatic_ktype: string | null;
  code_version: string;
  evidence?: Record<string, unknown> | null;
}

/** A car's choices, from the current one backwards. */
export interface KTypeChoiceHistory {
  vehicle_id: string;
  current_choice_id: string | null;
  entries: KTypeChoiceHistoryEntry[];
}

// --- A person's corrections to one car's data (`/v1/vehicles/{id}/corrections`) -------

export type FactCorrectionAction = 'set' | 'ignore' | 'withdraw';

/** The head of one field's correction chain on a car. */
export interface FactCorrectionState {
  field: string;
  /** `set`: `value` is in force. `ignored`: the car's value is not used. `withdrawn`: no correction. */
  status: 'set' | 'ignored' | 'withdrawn';
  correction_id: string;
  value: string | null;
  reviewer: string;
  reason: string | null;
  created_at: string;
  /** What the matcher used for the field when the correction was made, and where it came from. */
  previous_value: string | null;
  previous_source: string | null;
  group_id: string | null;
  /** The decision for several cars that wrote the correction; null for one car's own. */
  decision?: CorrectionDecisionRef | null;
  history_count: number;
}

/**
 * One field of the car a person may correct, with what the matcher uses for it today.
 * The editor is built from `type` and `values`, so a field the server adds needs no web change.
 */
export interface CorrectableField {
  field: string;
  label: string;
  /** `list`: several of `values` at once (a car's fuels), carried as one comma-joined string. */
  type: 'text' | 'integer' | 'list';
  /** The closed vocabulary; empty when any value is accepted. */
  values: string[];
  /**
   * The matcher evidence keys that belong to the field, e.g. `year` for `production_year`.
   * A candidate's key or a reason code is the field's when it equals one or starts with one + `_`.
   */
  evidence_keys: string[];
  /** As text; null when the car has no value or its value is ignored. */
  current_value: string | null;
  /** `registry`, `ais`, `review`, `rule`, `derived` or `correction`. */
  current_source: string | null;
  /** Values the listed candidates carry for the field, without the current one. */
  suggestions: string[];
}

export interface FactCorrectionRequest {
  /** Minted once per action and resent unchanged on a retry; becomes the correction id. */
  operation_id: string;
  field: string;
  action: FactCorrectionAction;
  /** Always text (an integer as digits, a list comma-joined); null unless `action` is `set`. */
  value: string | null;
  reviewer: string;
  reason?: string | null;
  /** The field's correction the screen showed (also a withdrawn one), or null without one. */
  supersedes_correction_id: string | null;
  evidence_fingerprint?: string | null;
  /**
   * Sent with the same operation id after a 409 `confirmation_required`: the person saw that
   * the car, resolved today, would lose or change its KType, and saves anyway.
   */
  confirm_change?: boolean;
}

export interface FactCorrectionHistoryEntry {
  correction_id: string;
  action: FactCorrectionAction;
  value: string | null;
  reviewer: string;
  reason: string | null;
  created_at: string;
  supersedes_correction_id: string | null;
  previous_value: string | null;
  previous_source: string | null;
  group_id: string | null;
  catalog_batch: string;
  automatic_terminal: string;
  automatic_ktype: string | null;
  code_version: string;
}

/** A car's corrections: one chain per corrected field, each from the current row backwards. */
export interface FactCorrectionHistory {
  vehicle_id: string;
  fields: {
    field: string;
    current_correction_id: string | null;
    entries: FactCorrectionHistoryEntry[];
  }[];
}

// --- One correction for several cars ----------------------------------------------------
// (`/v1/vehicles/{id}/corrections/scopes`, `.../corrections/preview`, `/v1/vehicle-corrections`)
// Nothing is saved for more than one car before a check of what it would change, car by car.

/** Where the matcher ends for one car: its terminal, and the KType when it resolves. */
export interface CarMatch {
  terminal: string;
  ktype: string | null;
}

/** One clause of a scope on a vehicle column; `is_empty` (no value) takes no values. */
export interface CorrectionScopeCondition {
  field: string;
  operator: VehicleOperator | 'is_empty';
  values: string[];
}

/** A condition a person may add to a wide scope, prefilled with this car's own value. */
export interface CorrectionNarrowable {
  field: string;
  label: string;
  /** As text; null when this car has no value there. */
  value: string | null;
}

/** Whom a correction could apply to. `this_car` carries nothing but its kind. */
export interface CorrectionScopeOption {
  kind: 'this_car' | 'same_data' | 'like_this';
  /** The cars in plain words, e.g. "All Volvo V70 cars with no drive type". */
  label?: string;
  /** Cars the scope covers, this one included; null when counting them took too long. */
  count?: number | null;
  too_broad?: boolean;
  /** `like_this` only: which of the field's groups this is, 0 the narrowest; sent back with the check. */
  rung?: number | null;
  conditions?: CorrectionScopeCondition[];
  narrowable?: CorrectionNarrowable[];
}

/** A scope that reaches beyond this car: nothing is saved for it before a check. */
export type CorrectionWideScope = CorrectionScopeOption & { kind: 'same_data' | 'like_this' };

export interface CorrectionScopesRequest {
  field: string;
  action: 'set' | 'ignore';
  /** As in a correction: text, and null unless `action` is `set`. */
  value: string | null;
}

export interface CorrectionScopes {
  scopes: CorrectionScopeOption[];
}

/** The scope a person picked beyond this car, with the conditions that narrow it. */
export interface CorrectionScopeChoice {
  option: CorrectionWideScope;
  narrow: CorrectionScopeCondition[];
}

export interface CorrectionPreviewRequest {
  field: string;
  action: 'set' | 'ignore';
  value: string | null;
  /**
   * The picked option by what the scopes call gave it -- `like_this` may come as several
   * options, told apart by `rung` and `conditions` -- with what narrows it.
   */
  scope: {
    kind: 'same_data' | 'like_this';
    rung: number | null;
    conditions: CorrectionScopeCondition[] | null;
    narrow: CorrectionScopeCondition[];
  };
  /** The lookup's fingerprint of the car the correction was entered on. */
  evidence_fingerprint: string;
}

/** What a correction would do to one checked car; every car has exactly one. */
export type CorrectionOutcome =
  | 'gained'
  | 'lost'
  | 'moved'
  | 'same'
  | 'worse'
  | 'still_unresolved'
  | 'no_effect'
  | 'already_corrected'
  | 'not_like_this';

export interface CorrectionPreviewCounts extends Record<CorrectionOutcome, number> {
  /** Checked cars whose KType a person chose; the choice stays. */
  with_choice: number;
  /** Of those, the cars the matcher would then resolve to another KType than the chosen one. */
  choice_would_disagree: number;
  /** The `still_unresolved` cars by the terminal they end on. */
  still_unresolved_by_terminal: Record<string, number>;
}

/** The check of a correction on the cars of a scope: a job on the server, polled. */
export interface CorrectionPreviewJob {
  preview_id: string;
  status: 'running' | 'done' | 'failed' | 'cancelled';
  field: string;
  action: 'set' | 'ignore';
  value: string | null;
  scope: { kind: 'same_data' | 'like_this'; label: string };
  /** Cars the scope covers; at most `cap` of them are checked. */
  affected: number;
  cap: number;
  checked: number;
  /** Every affected car was checked: no cap, no time-out, not stopped. Only then can it be applied. */
  complete: boolean;
  stopped_by: string | null;
  /** Partial while running, final once it has ended. */
  counts: CorrectionPreviewCounts;
  /** For the `gained` cars: whether the new KType's engines include the car's engine code. */
  engine_check: { agree: number; differ: number; unchecked: number };
  /** Cars an apply would write when the harmed ones (`lost`, `moved`, `worse`) are left out. */
  would_write: number;
  can_apply: boolean;
  /** `not_all_cars_checked`, `nothing_to_apply`, `harms_more_than_it_fixes`, `preview_expired`. */
  blocked_by: string[];
  /** Set when `status` is `failed`. */
  error?: string | null;
}

/** One checked car, as the lists behind the counts show it. */
export interface CorrectionPreviewCar {
  vehicle_id: string;
  plate: string | null;
  before: CarMatch | null;
  after: CarMatch | null;
}

export interface CorrectionPreviewCars {
  /** Checked cars of the outcome asked for; `cars` holds at most `limit` of them. */
  total?: number;
  cars: CorrectionPreviewCar[];
}

export interface CorrectionDecisionRequest {
  /** Minted once per action and resent unchanged on a retry; becomes the decision's event id. */
  operation_id: string;
  preview_id: string;
  /** `apply` writes the checked cars; `propose` keeps the decision and writes no car. */
  event: 'apply' | 'propose';
  /** Also write the cars that would lose or change their KType or get harder to match. */
  include_changed: boolean;
  reviewer: string;
  /** Required to apply. */
  reason: string | null;
}

export interface CorrectionWithdrawRequest {
  operation_id: string;
  reviewer: string;
  reason: string;
}

/** What an apply or a proposal did. */
export interface CorrectionDecisionResult {
  decision_id: string;
  status: 'proposed' | 'applied' | 'withdrawn';
  /** Cars written; 0 for a proposal. */
  written: number;
  /** Checked cars an apply left as they are when it looked again. */
  skipped?: { changed_since_check?: number; corrected_meanwhile?: number };
  counts?: CorrectionPreviewCounts;
  scope_label?: string;
  /** The written cars by the outcome the check gave them. */
  written_by_outcome?: Partial<Record<CorrectionOutcome, number>>;
}

/** What undoing a decision did. */
export interface CorrectionWithdrawal {
  decision_id: string;
  status: 'proposed' | 'applied' | 'withdrawn';
  /** Cars whose correction was taken back. */
  withdrawn: number;
  /** Cars a person changed since: left as they are. */
  left_changed: number;
  member_count?: number;
  scope_label?: string;
}

/** One step in a many-car decision's life: proposed, applied, undone. */
export interface CorrectionDecisionEvent {
  event_id: string;
  event: 'propose' | 'apply' | 'withdraw';
  reviewer: string;
  reason: string | null;
  created_at: string;
}

/** One decision for several cars, as the Decisions overview lists it. */
export interface CorrectionDecisionSummary {
  decision_id: string;
  status: 'proposed' | 'applied' | 'withdrawn';
  field: string;
  /** The field in words ("Engine code"). */
  field_label?: string;
  action: 'set' | 'ignore';
  value: string | null;
  /** Which cars, in the sentence the person saw when deciding. */
  scope_label: string;
  manufacturer: string;
  model_family: string | null;
  reviewer: string;
  reason: string | null;
  created_at: string;
  /** Cars the decision wrote; 0 for a proposal. */
  member_count: number;
  /** The check the decision rests on: `affected`, `checked`, `gained`, `lost`, `moved`, `worse`. */
  measurement: Record<string, unknown>;
  events: CorrectionDecisionEvent[];
}

export interface CorrectionDecisionList {
  decisions: CorrectionDecisionSummary[];
}

/** The decision for several cars a car's correction came from. */
export interface CorrectionDecisionRef {
  decision_id: string;
  scope_label: string;
  member_count: number;
  reviewer: string;
}

/**
 * Where a car stands with matching, read from stored results. A person's choice
 * outranks the matcher (`chosen`, `chosen_none`); `not_evaluated` is a car with
 * no stored result yet.
 */
export type MatchResultState =
  | 'resolved'
  | 'several'
  | 'one_unconfirmed'
  | 'none'
  | 'not_matchable'
  | 'chosen'
  | 'chosen_none'
  | 'not_evaluated';

export interface MatchResultRun {
  run_id: string;
  mode: string;
  status: string;
  catalog_batch: string;
  matcher_version: string;
  target: number;
  evaluated: number;
  unchanged: number;
  started_at: string;
  finished_at: string | null;
}

/** Matching statistics of a Vehicles filter, from stored results -- the matcher is not run. */
export interface MatchResultOverview {
  total: number;
  states: Array<{ state: MatchResultState; cars: number }>;
  terminals: Array<{ value: string; cars: number }>;
  several_candidate_counts: Record<string, number>;
  candidate_limit: number;
  several_separating_fields: Array<{ field: string; cars: number }>;
  several_missing_fields: Array<{ field: string; cars: number }>;
  none_conflicting_fields: Array<{ field: string; cars: number }>;
  none_without_candidates: number;
  not_matchable_reasons: Array<{ reason: string; cars: number }>;
  /** Cars whose vehicle changed after it was matched: the stored result may be out of date. */
  changed_since_matched: number;
  catalog_batches: Array<{ value: string; cars: number }>;
  matcher_versions: Array<{ value: string; cars: number }>;
  latest_run: MatchResultRun | null;
}

/** Cars per state under a filter: the strip above the car list. */
export interface MatchResultCounts {
  total: number;
  states: Array<{ state: MatchResultState; cars: number }>;
  changed_since_matched: number;
  /** The changed cars are being matched again on the server right now. */
  refreshing?: boolean;
}

/** One reviewer rule from the TS data screen, as a change to many cars. */
export interface ReviewerRuleChange {
  rule_id: string;
  status: string;
  author: string;
  applied_by: string | null;
  retired_by: string | null;
  created_at: string;
  applied_at: string | null;
  retired_at: string | null;
  /** The rule's conditions in words, e.g. "brand contains KIA and is_4wd = 0". */
  conditions: string;
  target_field: string;
  target_value: string;
  override: boolean;
  note: string | null;
  records_written: number;
  vehicles: number;
  /** Vehicles changed after their match result was stored. */
  out_of_date: number;
}

/** One count of the breakdown, as a filter of the car list: a state and a clause on its cars. */
export interface MatchResultCause {
  state: MatchResultState;
  /** A `match_*` filter field of the Vehicles list. */
  field: string;
  value: string;
  /** How the filter reads on screen, e.g. "the car has no engine code". */
  label: string;
}
