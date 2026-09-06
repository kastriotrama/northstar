import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { API_BASE_URL } from './api-config';
import type {
  CoverageBatch,
  DiscriminatorReport,
  MatchChunkBuild,
  MatchReviewPatternPage,
  MatchRunSummary,
  PatternReport,
  PopulationAttributes,
  RefineResult,
  ResolutionRule,
  RuleAdvice,
  RuleCatalogResponse,
  RuleCondition,
  RulePreview,
  SourceBatch,
  SourceFieldInventory,
  SourceRecordDetail,
  SourceRecordPage,
  TargetVocabulary,
  TecDocCoverageReport,
  TecDocEntityPage,
  TecDocPage,
  TsCoverageReport,
  UnresolvedOverview,
} from './models';

/** Drops null/undefined/empty values so optional filters stay out of the query string. */
function params(source: Record<string, string | number | null | undefined>): HttpParams {
  let result = new HttpParams();
  for (const [key, value] of Object.entries(source)) {
    if (value === null || value === undefined || value === '') {
      continue;
    }
    result = result.set(key, String(value));
  }
  return result;
}

@Injectable({ providedIn: 'root' })
export class Api {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  // --- Page 1: raw Transportstyrelsen staging rows -------------------------------------
  // Paging is keyset-based: pass the previous page's `next_cursor`, never an offset.
  listSourceRecords(options: {
    query?: string;
    field?: string | null;
    value?: string | null;
    batchId?: string | null;
    cursor?: number | null;
    limit?: number;
  }): Observable<SourceRecordPage> {
    return this.http.get<SourceRecordPage>(`${this.base}/v1/source-records/ts`, {
      params: params({
        query: options.query,
        field: options.field,
        value: options.value,
        batch_id: options.batchId,
        cursor: options.cursor,
        limit: options.limit ?? 100,
      }),
    });
  }

  getSourceRecord(recordId: number): Observable<SourceRecordDetail> {
    return this.http.get<SourceRecordDetail>(`${this.base}/v1/source-records/ts/${recordId}`);
  }

  listSourceBatches(): Observable<{ items: SourceBatch[] }> {
    return this.http.get<{ items: SourceBatch[] }>(`${this.base}/v1/source-records/ts/batches`);
  }

  sourceFieldInventory(): Observable<SourceFieldInventory> {
    return this.http.get<SourceFieldInventory>(`${this.base}/v1/source-records/ts/fields`);
  }

  // --- Page 2: TecDoc ------------------------------------------------------------------
  listTecDocVehicles(options: {
    query?: string;
    limit?: number;
    offset?: number;
  }): Observable<TecDocPage> {
    return this.http.get<TecDocPage>(`${this.base}/v1/normalization-review/tecdoc/vehicles`, {
      params: params({
        query: options.query,
        limit: options.limit ?? 100,
        offset: options.offset ?? 0,
      }),
    });
  }

  listTecDocEntities(options: {
    kind: string;
    query?: string;
    limit?: number;
    offset?: number;
  }): Observable<TecDocEntityPage> {
    return this.http.get<TecDocEntityPage>(`${this.base}/v1/normalization-review/tecdoc/entities`, {
      params: params({
        kind: options.kind,
        query: options.query,
        limit: options.limit ?? 100,
        offset: options.offset ?? 0,
      }),
    });
  }

  // --- Page 3: rules -------------------------------------------------------------------
  // Uses the paginated catalog, not GET /rules: that endpoint returns ~12MB in ~25s
  // because it also aggregates the newest normalization batch.
  listRules(options: {
    query?: string;
    area?: string | null;
    canonicalField?: string | null;
    decision?: string | null;
    limit?: number;
    offset?: number;
  }): Observable<RuleCatalogResponse> {
    return this.http.get<RuleCatalogResponse>(
      `${this.base}/v1/normalization-review/rules/catalog`,
      {
        params: params({
          query: options.query,
          area: options.area,
          canonical_field: options.canonicalField,
          decision: options.decision,
          limit: options.limit ?? 100,
          offset: options.offset ?? 0,
        }),
      },
    );
  }

  // --- Page 4: coverage ----------------------------------------------------------------
  listCoverageBatches(): Observable<{ items: CoverageBatch[] }> {
    return this.http.get<{ items: CoverageBatch[] }>(`${this.base}/v1/coverage/batches`);
  }

  tsCoverage(batchId?: string | null): Observable<TsCoverageReport> {
    return this.http.get<TsCoverageReport>(`${this.base}/v1/coverage/ts`, {
      params: params({ batch_id: batchId }),
    });
  }

  tecdocCoverage(): Observable<TecDocCoverageReport> {
    return this.http.get<TecDocCoverageReport>(`${this.base}/v1/coverage/tecdoc`);
  }

  // --- Page 5: chunks ------------------------------------------------------------------
  matchReviewSummary(): Observable<MatchRunSummary> {
    return this.http.get<MatchRunSummary>(`${this.base}/v1/match-review/summary`);
  }

  listMatchReviewPatterns(operationId: string, category?: string | null): Observable<MatchReviewPatternPage> {
    return this.http.get<MatchReviewPatternPage>(`${this.base}/v1/match-review/patterns`, {
      params: params({ operation_id: operationId, category }),
    });
  }

  decideMatchReviewPattern(
    operationId: string,
    patternKey: string,
    body: { action: string; reviewer: string; reason: string; selected_values: string[] },
  ): Observable<unknown> {
    return this.http.post<unknown>(
      `${this.base}/v1/match-review/patterns/${encodeURIComponent(patternKey)}/decision`,
      body,
      { params: params({ operation_id: operationId }) },
    );
  }

  // --- Unresolved fields: population-first rule authoring ------------------------------
  // The screen drives itself from `refineRule`: one call returns the counts, the facets
  // and whether the population is coherent yet, so every edit needs exactly one request.

  listMatchChunkBuilds(): Observable<MatchChunkBuild[]> {
    return this.http.get<MatchChunkBuild[]>(`${this.base}/v1/match-review/builds`);
  }

  unresolvedOverview(buildId: string): Observable<UnresolvedOverview> {
    return this.http.get<UnresolvedOverview>(`${this.base}/v1/match-review/unresolved`, {
      params: params({ build_id: buildId }),
    });
  }

  unresolvedDiscriminators(options: {
    buildId: string;
    sourceField: string;
    sourceValue: string;
  }): Observable<DiscriminatorReport> {
    return this.http.get<DiscriminatorReport>(
      `${this.base}/v1/match-review/unresolved/discriminators`,
      {
        params: params({
          build_id: options.buildId,
          source_field: options.sourceField,
          source_value: options.sourceValue,
        }),
      },
    );
  }

  unresolvedAttributes(options: {
    buildId: string;
    sourceField: string;
    sourceValue: string;
  }): Observable<PopulationAttributes> {
    return this.http.get<PopulationAttributes>(
      `${this.base}/v1/match-review/unresolved/attributes`,
      {
        params: params({
          build_id: options.buildId,
          source_field: options.sourceField,
          source_value: options.sourceValue,
        }),
      },
    );
  }

  unresolvedValuePatterns(options: {
    buildId: string;
    sourceField: string;
    sourceValue: string;
    fieldName: string;
  }): Observable<PatternReport> {
    return this.http.get<PatternReport>(`${this.base}/v1/match-review/unresolved/patterns`, {
      params: params({
        build_id: options.buildId,
        source_field: options.sourceField,
        source_value: options.sourceValue,
        field_name: options.fieldName,
      }),
    });
  }

  /** Suggests a rule. Writes nothing -- the proposal still has to be previewed. */
  adviseRule(body: {
    build_id: string;
    source_field: string;
    source_value: string;
  }): Observable<RuleAdvice> {
    return this.http.post<RuleAdvice>(`${this.base}/v1/match-review/unresolved/advise`, body);
  }

  targetVocabulary(buildId: string, targetField: string): Observable<TargetVocabulary> {
    return this.http.get<TargetVocabulary>(`${this.base}/v1/match-review/target-vocabulary`, {
      params: params({ build_id: buildId, target_field: targetField }),
    });
  }

  /** Live counts and facets for the predicate as it stands. Writes nothing. */
  refineRule(body: {
    build_id: string;
    source_field: string;
    source_value: string;
    conditions: RuleCondition[];
  }): Observable<RefineResult> {
    return this.http.post<RefineResult>(`${this.base}/v1/match-review/unresolved/refine`, body);
  }

  /** Dry run: counts what the rule would resolve. Writes nothing. */
  previewRule(body: {
    build_id: string;
    conditions: RuleCondition[];
    target_field: string;
    target_value: string;
  }): Observable<RulePreview> {
    return this.http.post<RulePreview>(`${this.base}/v1/match-review/rule-preview`, body);
  }

  /** Keeps a previewed rule. Saving alone resolves nothing; running it does. */
  saveResolutionRule(body: {
    build_id: string;
    source_field: string;
    source_value: string;
    conditions: RuleCondition[];
    target_field: string;
    target_value: string;
    author: string;
    note: string | null;
  }): Observable<ResolutionRule> {
    return this.http.post<ResolutionRule>(`${this.base}/v1/match-review/resolution-rules`, body);
  }

  listResolutionRules(options: {
    buildId: string;
    sourceField?: string | null;
    sourceValue?: string | null;
  }): Observable<ResolutionRule[]> {
    return this.http.get<ResolutionRule[]>(`${this.base}/v1/match-review/resolution-rules`, {
      params: params({
        build_id: options.buildId,
        source_field: options.sourceField,
        source_value: options.sourceValue,
      }),
    });
  }

  /** Runs a saved rule over the build: one resolution per car it still covers. */
  applyResolutionRule(ruleId: string, reviewer: string): Observable<ResolutionRule> {
    return this.http.post<ResolutionRule>(
      `${this.base}/v1/match-review/resolution-rules/${encodeURIComponent(ruleId)}/apply`,
      { reviewer },
    );
  }

  /** Undoes a run: the rows it resolved reopen, the record of the rule stays. */
  retireResolutionRule(ruleId: string, reviewer: string): Observable<ResolutionRule> {
    return this.http.post<ResolutionRule>(
      `${this.base}/v1/match-review/resolution-rules/${encodeURIComponent(ruleId)}/retire`,
      { reviewer },
    );
  }
}
