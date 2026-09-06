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

export interface MatchChunk {
  chunk_id: string;
  [key: string]: unknown;
}

export interface MatchChunkPage {
  items: MatchChunk[];
  total?: number;
  filtered_total?: number;
  limit?: number;
  offset?: number;
  [key: string]: unknown;
}

/** Advisor proposal returned by the match-review rule advisor. */
export interface RuleAdvice {
  [key: string]: unknown;
}
